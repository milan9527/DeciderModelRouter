"""Refresh exact AWS tariffs without changing benchmark scores or measurements."""

import argparse
import gzip
import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from app.bedrock_evidence import digest

OFFERS = ("AmazonBedrock", "AmazonBedrockFoundationModels")
ALIASES = {
    "amazon.titan-embed-image-v1": "Titan Embeddings G1 Image",
    "amazon.titan-embed-text-v1": "Titan Embeddings G1 Text",
    "amazon.titan-embed-text-v2:0": "TitanEmbeddingsV2-Text-input",
    "amazon.nova-2-lite-v1:0": "Nova 2.0 Lite",
    "amazon.nova-2-sonic-v1:0": "Nova Sonic 2.0",
    "amazon.nova-2-multimodal-embeddings-v1:0": "Nova MME",
    "deepseek.r1-v1:0": "R1",
    "mistral.devstral-2-123b": "Devstral",
    "mistral.magistral-small-2509": "Magistral Small 1.2",
    "mistral.ministral-3-3b-instruct": "Ministral 3B 3.0",
    "mistral.ministral-3-8b-instruct": "Ministral 8B 3.0",
    "mistral.mistral-large-2402-v1:0": "Mistral Large",
    "mistral.mistral-small-2402-v1:0": "Mistral Small",
    "mistral.voxtral-mini-3b-2507": "Voxtral Mini 1.0",
    "mistral.voxtral-small-24b-2507": "Voxtral Small 1.0",
    "nvidia.nemotron-nano-9b-v2": "NVIDIA Nemotron Nano 2",
    "nvidia.nemotron-nano-12b-v2": "NVIDIA Nemotron Nano 2 VL",
    "qwen.qwen3-32b-v1:0": "Qwen3 32B",
    "qwen.qwen3-coder-30b-a3b-v1:0": "Qwen3 Coder 30B A3B",
    "twelvelabs.marengo-embed-2-7-v1:0": "Marengo Embed 2.7",
    "twelvelabs.pegasus-1-2-v1:0": "Pegasus 1.2",
    "twelvelabs.pegasus-1-5-v1:0": "TwelveLabs Pegasus 1.5",
    "xai.grok-4.6": "xai.grok-4.6",
}
for size in (4, 12, 27):
    ALIASES[f"google.gemma-3-{size}b-it"] = f"Gemma 3 {size}B"
for generation, sizes in (("3", (8, 70)), ("3-1", (8, 70)), ("3-3", (70,))):
    for size in sizes:
        ALIASES[f"meta.llama{generation}-{size}b-instruct-v1:0"] = (
            f"Llama {generation.replace('-', '.')} {size}B")
for family in ("scout", "maverick"):
    ALIASES[f"meta.llama4-{family}-17b-instruct-v1:0"] = f"Llama 4 {family.title()} 17B"
for family in ("mistral-7b", "mixtral-8x7b"):
    version = "v0:2" if family == "mistral-7b" else "v0:1"
    ALIASES[f"mistral.{family}-instruct-{version}"] = family.replace("-", " ")

IMAGE_OPERATIONS = {
    "conservative-upscale": "upscaleconservative", "creative-upscale": "upscalecreative",
    "fast-upscale": "upscalefast", "image-control-sketch": "controlsketch",
    "image-control-structure": "controlstructure", "image-erase-object": "eraseobject",
    "image-inpaint": "inpaint", "image-remove-background": "removebg",
    "image-search-recolor": "recolor", "image-search-replace": "searchreplace",
    "image-style-guide": "styleguide", "outpaint": "outpaint", "style-transfer": "styletransfer",
}
INFERENCE_KEYS = {
    "input tokens": "input_price", "output tokens": "output_price",
    "prompt cache read input tokens": "cache_read_price",
    "prompt cache write input tokens": "cache_write_price",
}


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def product_name(model):
    mid = model["id"]
    if mid.startswith("openai.gpt-") and "oss" not in mid:
        return "OpenAI " + model["name"]
    if mid.startswith("stability.stable-"):
        return "Stability AI Image Services"
    return ALIASES.get(mid, model["name"])


def compile_price(model, documents, region, context=None):
    invoke = model.get("invoke_id")
    if not invoke:
        return None
    name = product_name(model)
    tariffs = []
    for document in documents:
        for sku, product in document["products"].items():
            attrs = product["attributes"]
            if attrs.get("regionCode") != region or normalized(
                attrs.get("model", attrs.get("titanModel", ""))
            ) != normalized(name):
                continue
            usage = attrs.get("usagetype", "").lower()
            if any(part in usage for part in (
                "mantle", "batch", "custom", "latency-optimized", "priority", "flex",
                "ultrafast", "reserved", "provisioned", "training",
            )):
                continue
            if attrs.get("feature") not in (None, "On-demand Inference"):
                continue
            if ("global" in usage) != invoke.startswith("global."):
                continue
            if model["id"].startswith("stability.stable-"):
                operation = model["id"].removeprefix("stability.stable-").removesuffix("-v1:0")
                expected = IMAGE_OPERATIONS.get(operation)
                if not expected or "createdimage" + expected not in normalized(usage):
                    continue
            inference = attrs.get("inferenceType", "")
            key = INFERENCE_KEYS.get(inference.lower())
            tier = "long_context" if "long_ctx" in usage else "standard"
            ttl = 3600 if "1h" in usage else 1800 if "30m" in usage else 300
            for term in document["terms"].get("OnDemand", {}).get(sku, {}).values():
                for dimension in term["priceDimensions"].values():
                    if dimension["beginRange"] != "0" or dimension["endRange"] != "Inf":
                        continue
                    amount = Decimal(dimension["pricePerUnit"]["USD"])
                    if not amount.is_finite() or amount < 0:
                        raise ValueError(f"Invalid AWS price for {sku}")
                    scale = {"1k tokens": 1000, "1m tokens": 1, "tokens": 1_000_000}.get(
                        dimension["unit"].lower())
                    tariffs.append({
                        "sku": sku, "rate_code": dimension["rateCode"],
                        "offer": document["offerCode"], "model": attrs.get("model", attrs.get("titanModel")),
                        "usage_type": attrs["usagetype"], "inference_type": inference,
                        "unit": dimension["unit"], "usd_per_unit": str(amount),
                        "key": key if scale else None, "context_tier": tier,
                        "usd_per_million_tokens": float(amount * scale) if scale else None,
                        "cache_write_ttl_seconds": ttl if key == "cache_write_price" else None,
                        "effective_date": term.get("effectiveDate"),
                        "source": document["_source"],
                        "publication_date": document["publicationDate"],
                    })
    if not tariffs:
        return None
    groups = defaultdict(list)
    for tariff in tariffs:
        if tariff["key"]:
            key = tariff["key"]
            if key == "cache_write_price":
                key += "_" + str(tariff["cache_write_ttl_seconds"])
            groups[tariff["context_tier"], key].append(tariff["usd_per_million_tokens"])
    rates = {"standard": {}, "long_context": {}}
    for (tier, key), values in groups.items():
        if len(set(values)) != 1:
            raise ValueError(f"Conflicting AWS rates for {model['id']} {tier}/{key}: {values}")
        rates[tier][key] = values[0]
    standard = rates["standard"]
    result = {
        **standard, "rates": rates, "tariffs": tariffs, "currency": "USD",
        "region": region, "invoke_id": invoke, "matched_name": name,
        "inference_scope": "global" if invoke.startswith("global.") else "us_cross_region"
        if invoke.startswith("us.") else "regional",
        "service_tier": "standard", "scope": "AWS on-demand list price; USD per million tokens where applicable",
        "source": tariffs[0]["source"], "skus": sorted({t["sku"] for t in tariffs}),
        "publication_date": max(t["publication_date"] for t in tariffs),
        "long_context_threshold": context.get("threshold") if context else None,
        "context_source": context.get("url") if context else None,
        "default_cache_write_ttl_seconds": 1800 if model["id"].startswith("openai.gpt-")
        and "oss" not in model["id"] else 300,
    }
    if rates["long_context"] and not result["long_context_threshold"]:
        raise ValueError(f"Long-context threshold must be documented: {model['id']}")
    if model["id"] == "openai.gpt-5.6-sol" and result["publication_date"] < "2026-12-01":
        result["valid_until"] = "2026-12-01T00:00:00Z"
        result["promotion_source"] = "https://aws.amazon.com/bedrock/pricing/"
    return result


def load_documents(folder, region, download=False):
    documents = []
    for offer in OFFERS:
        url = f"https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{offer}/current/{region}/index.json"
        path = folder / (offer + ".json.gz")
        if download:
            response = httpx.get(url, timeout=90, trust_env=False)
            response.raise_for_status()
            path.write_bytes(gzip.compress(response.content, mtime=0))
        document = json.loads(gzip.decompress(path.read_bytes()))
        document["_source"] = url
        documents.append(document)
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--catalog", type=Path, default=Path("app/bedrock_catalog.json"))
    parser.add_argument("--evidence", type=Path, default=Path("app/bedrock_evaluations.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.sources.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(args.catalog.read_text())
    documents = load_documents(args.sources, catalog["region"], args.download)
    contexts = json.loads((args.sources / "context-thresholds.json").read_text())
    evidence = json.loads(args.evidence.read_text())
    updates, missing = {}, []
    for model in catalog["models"]:
        pricing = compile_price(model, documents, catalog["region"], contexts.get(model["id"]))
        if pricing:
            entry = evidence["entries"].setdefault(model["id"], {"invoke_id": model["invoke_id"]})
            if entry.get("invoke_id") != model["invoke_id"]:
                raise ValueError("Measured endpoint differs from current catalog")
            entry["pricing"] = pricing
            updates[model["id"]] = {k: pricing.get(k) for k in
                                   ("input_price", "output_price", "matched_name", "inference_scope")}
        else:
            missing.append(model["id"])
    evidence["pricing_snapshot"] = {
        "captured_at": datetime.now(UTC).isoformat(),
        "sources": [{"url": d["_source"], "publication_date": d["publicationDate"],
                     "sha256": hashlib.sha256(
                         gzip.decompress((args.sources / (d["offerCode"] + ".json.gz")).read_bytes())
                     ).hexdigest()} for d in documents],
        "models_with_tariffs": len(updates),
    }
    evidence["version"] = digest(evidence)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    report = {"updated": updates, "missing": missing, "version": evidence["version"]}
    (args.sources / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"models_with_tariffs": len(updates), "missing": missing}, ensure_ascii=False))


if __name__ == "__main__":
    main()
