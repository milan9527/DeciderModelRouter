"""Compile measured endpoints without replacing raw outputs or inventing missing scores."""

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.bedrock_evidence import digest
from app.gateway_effect_judge import grade
from app.routing_quota import quota_exclusion
from scripts.measure_bedrock_catalog import code_grade
from scripts.measure_bedrock_specialized import native_suite


def normalize_answer(text, kind):
    text = text.strip()
    fences = re.findall(r"```(?:python|json)?\s*\n?([\s\S]*?)```", text)
    if len(fences) == 1:
        return fences[0].strip()
    if kind == "json":
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])\s*$", text)
        if match:
            return match[1]
    if kind in {"number", "fraction"}:
        match = re.search(r"\\boxed\{\\frac\{(\d+)\}\{(\d+)\}\}\s*(?:\\\])?\s*$", text)
        if match:
            return match[1] + "/" + match[2]
        match = re.search(r"\\boxed\{([^{}]+)\}\s*(?:\\\])?\s*$", text)
        if match:
            return match[1].strip()
        last = text.splitlines()[-1].strip().strip("$")
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:/\d+)?", last):
            return last
    return None


def content_grade(row, case, image):
    if row["status"] != "completed" or not row["output"].get("complete"):
        return row["grade"]
    if row["grade"]["status"] == "pass" or case is None or row["workflow"] == "general_text":
        return row["grade"]
    kind = "code" if row["workflow"] == "coding" else case["judge"]["kind"]
    normalized = normalize_answer(row["output"]["text"], kind)
    if normalized is None:
        return {"status": "pending", "source": "ambiguous_content",
                "reason": "格式不合规，尚不能自动确定最终内容；严格验收仍保留原失败"}
    output = {**row["output"], "text": normalized}
    result = code_grade(case, output, image) if kind == "code" else grade(case, output)
    return {**result, "normalization": "single_code_fence_or_explicit_final_answer",
            "normalized_sha256": hashlib.sha256(normalized.encode()).hexdigest()}


def price(model, pricing):
    if pricing is None:
        return None
    def normalized(value):
        return re.sub(r"[^a-z0-9]", "", value.lower())
    found = defaultdict(list)
    for sku, product in pricing["products"].items():
        attrs = product["attributes"]
        usage = attrs.get("usagetype", "").lower()
        if attrs.get("feature") != "On-demand Inference" or any(
            part in usage for part in ("mantle", "batch", "custom", "latency-optimized", "priority", "flex")
        ):
            continue
        if normalized(attrs.get("model", "")) != normalized(model["name"]):
            continue
        inference = attrs.get("inferenceType", "").lower()
        key = {"input tokens": "input_price", "output tokens": "output_price",
               "prompt cache read input tokens": "cache_read_price",
               "prompt cache write input tokens": "cache_write_price"}.get(inference)
        if key is None:
            continue
        # Global inference is a different price scope from US and regional inference.
        if "global" in usage and not (model.get("invoke_id") or "").startswith("global."):
            continue
        for term in pricing["terms"]["OnDemand"].get(sku, {}).values():
            for dimension in term["priceDimensions"].values():
                if dimension["beginRange"] != "0" or dimension["endRange"] != "Inf":
                    continue
                unit = dimension["unit"].lower()
                scale = {"1k tokens": 1000, "1m tokens": 1, "tokens": 1_000_000}.get(unit)
                if scale:
                    found[key].append((float(Decimal(dimension["pricePerUnit"]["USD"]) * scale), sku))
    if not all(key in found and len({r[0] for r in found[key]}) == 1 for key in ("input_price", "output_price")):
        return None
    return {**{key: values[0][0] for key, values in found.items() if len({v[0] for v in values}) == 1},
            "skus": sorted({sku for values in found.values() for _, sku in values}),
            "source": "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/current/us-east-1/index.json",
            "publication_date": pricing.get("publicationDate"), "region": "us-east-1",
            "scope": "AWS on-demand list price; not an invoice; USD per million tokens"}


def compile_evidence(directory, image, pricing=None):
    manifest = json.loads((directory / "manifest.json").read_text())
    cases = {c["id"]: c for c in manifest["suite"]["cases"]}
    observations = [json.loads(p.read_text()) for p in sorted((directory / "observations").glob("*.json"))]
    # The earlier full-message tool probe is retained as raw history but does not prove portability.
    observations = [r for r in observations if r["case_id"] != "native-tools"]
    existing = {(r["model_id"], r["case_id"]) for r in observations}
    replaced = {(r["model_id"], r["supersedes_case_id"]) for r in observations if r.get("supersedes_case_id")}
    superseded = [r for r in observations if (
        r["case_id"] == "image-edit" and (r["model_id"], "image-edit-v2") in existing
    ) or (
        r["case_id"] in {"retrieval-0", "retrieval-1"}
        and (r["model_id"], r["case_id"].replace("retrieval-", "retrieval-v2-")) in existing
    ) or (r["model_id"], r["case_id"]) in replaced]
    observations = [r for r in observations if r not in superseded]
    supplemental_data = native_suite()
    by_model = defaultdict(list)
    reviews = directory / "content-reviews"
    reviews.mkdir(exist_ok=True)
    for row in observations:
        review_path = reviews / (hashlib.sha256((row["model_id"] + row["case_id"] +
            json.dumps(row.get("output"), sort_keys=True)).encode()).hexdigest() + ".json")
        artifact_review = directory / "artifact-reviews" / (
            hashlib.sha256(row["model_id"].encode()).hexdigest()[:16] + "-" + row["case_id"] + ".json")
        if artifact_review.exists():
            review = json.loads(artifact_review.read_text())
            if review["output_sha256"] != hashlib.sha256(json.dumps(row["output"], sort_keys=True).encode()).hexdigest():
                raise ValueError("Artifact review belongs to a different output")
        elif review_path.exists():
            review = json.loads(review_path.read_text())
        else:
            review = content_grade(row, cases.get(row.get("supersedes_case_id", row["case_id"])), image)
            review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n")
        by_model[row["model_id"]].append({**row, "content_grade": review,
            "strict_grade": review if row["grade"]["status"] == "pending" else row["grade"]})
    entries = {}
    for model in manifest["catalog"]["models"]:
        rows = by_model[model["id"]]
        groups = defaultdict(list)
        for row in rows:
            groups[row["workflow"]].append(row)
        measurements = {}
        for workflow, sample in groups.items():
            times = sorted(r["latency_ms"] for r in sample)
            passed = sum(r["content_grade"]["status"] == "pass" for r in sample)
            scored = sum(r["content_grade"]["status"] in {"pass", "fail"} for r in sample)
            measurements[workflow] = {
                "suite": manifest["suite"]["version"] if workflow in {
                    "general_text", "reasoning", "coding", "vision"
                } else supplemental_data.get("version", manifest["suite"]["version"]),
                "samples": len(sample),
                "content_passed": passed, "content_scored": scored,
                "content_pending": len(sample) - scored,
                "strict_passed": sum(r["strict_grade"]["status"] == "pass" for r in sample),
                "strict_pending": sum(r["strict_grade"]["status"] == "pending" for r in sample),
                "call_errors": sum(r["status"] == "error" for r in sample),
                "p50_ms": times[math.ceil(len(times) * .5) - 1],
                "p95_ms": times[math.ceil(len(times) * .95) - 1],
                "scope": "visible development cases, small sample; not production success probability",
                "metric_description": {
                    "image": "合成图片编辑：检查尺寸、颜色或透明度，不代表审美质量",
                    "speech": "英语单句，独立语音识别核对；不评价音色自然度和其他语言",
                    "video_understanding": "一段花朵开放视频，助手对照采样画面复核；不代表广泛视频能力",
                    "embedding": "中英检索，每题3篇文档的首位命中；不是大型检索测试",
                    "tools": "一次可跨模型传递的工具调用与结果使用；不等于Agent整体验收",
                    "multi_turn": "3条消息的信息记忆检查；不代表长上下文能力",
                }.get(workflow, "共同开发测试题；内容正确与严格格式分开计数"),
            }
        caps = {}
        for workflow, capability in (("general_text", "converse"), ("multi_turn", "multi_turn"),
                                     ("vision", "vision"), ("video_understanding", "video_understanding"),
                                     ("embedding", "embedding"), ("image", "image"), ("speech", "speech"),
                                     ("audio_understanding", "audio_understanding"), ("rerank", "rerank"),
                                     ("safety", "converse")):
            if groups[workflow]:
                accepted = any(r["status"] == "completed" for r in groups[workflow])
                hard_failure = all(r.get("error", {}).get("code") in {
                    "ValidationException", "AccessDeniedException", "ResourceNotFoundException"
                } for r in groups[workflow])
                caps[capability] = True if accepted else False if hard_failure else None
        if groups["tools"]:
            caps["tools"] = any(r.get("output", {}).get("native_messages_supported") is True for r in groups["tools"])
        reason = ("账户可用性未确认" if not model["account_available"] else
                  "需要预置容量或部署" if not model["invoke_id"] else
                  quota_exclusion(model, manifest["catalog"]["region"]))
        entries[model["id"]] = {
            "invoke_id": model["invoke_id"], "status": "blocked" if reason else "measured" if rows else "unmeasured",
            "reason": reason or (None if rows else "尚缺对应专用接口的质量测量"),
            "configuration": {"request_fields": {}, "inference_config": {},
                              "max_output_tokens": min(r.get("configuration", {}).get("max_output_tokens", 4096)
                                                       for r in groups["general_text"])
                              if groups["general_text"] else 1024 if groups["speech"] else None,
                              "sampling": "provider defaults", "cache": False},
            "measurements": measurements, "capabilities": caps, "pricing": price(model, pricing),
            "errors": dict(Counter(r["error"]["code"] for r in rows if r.get("error"))),
        }
    data = {"schema_version": 1, "catalog_version": manifest["catalog_version"],
            "suite_version": manifest["suite"]["version"], "scope": "visible_development_suite_not_business_calibration",
            "captured_at": datetime.now(UTC).isoformat(), "entries": entries,
            "observations": len(observations),
            "superseded_observations": len(superseded),
            "supplemental_suite": supplemental_data,
            "source": "validation/independent_gateway/bedrock-evidence-2026-11-26/README.zh.md"}
    data["version"] = digest(data)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurements", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--code-image", required=True)
    parser.add_argument("--pricing", type=Path)
    args = parser.parse_args()
    data = compile_evidence(args.measurements, args.code_image,
                            json.loads(args.pricing.read_text()) if args.pricing else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"version": data["version"], "observations": data["observations"],
                      "status": dict(Counter(r["status"] for r in data["entries"].values())),
                      "priced_models": sum(bool(r["pricing"]) for r in data["entries"].values())}))


if __name__ == "__main__":
    main()
