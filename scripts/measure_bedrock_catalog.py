"""Measure exact Bedrock endpoints on a shared, resumable development suite."""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.bedrock_eval_suite import evaluation_suite
from app.bedrock_limits import output_limit
from app.gateway_effect_judge import grade
from app.routing_quota import quota_exclusion

ROOT = Path(__file__).resolve().parents[1]


def response_output(response):
    text = "\n".join(b["text"] for b in response["output"]["message"]["content"] if "text" in b)
    return {"text": text, "usage": response.get("usage"), "stop_reason": response.get("stopReason"),
            "complete": bool(text.strip()) and response.get("stopReason") == "end_turn",
            "request_id": response.get("ResponseMetadata", {}).get("RequestId")}


def code_grade(case, output, image):
    if not output["complete"]:
        return grade(case, output)
    with tempfile.TemporaryDirectory(prefix="bedrock-code-") as temporary:
        folder = Path(temporary)
        (folder / "input.json").write_text(json.dumps({"results": [{
            "id": "probe", "observation_id": hashlib.sha256(output["text"].encode()).hexdigest()[:24],
            "case_id": case["id"], "output": output,
        }]}))
        result = subprocess.run([
            sys.executable, str(ROOT / "scripts/check_effect_code.py"),
            "--evaluation", str(folder / "input.json"), "--output", str(folder / "result.json"),
            "--image", image,
        ], capture_output=True, text=True, timeout=40)
        if result.returncode:
            return {"status": "pending", "source": "grader_error", "reason": result.stderr[-500:]}
        row = json.loads((folder / "result.json").read_text())[0]
        return {"status": row["status"], "source": "isolated_tests", "result": row["result"],
                "image": row["image"], "source_sha256": row["source_sha256"]}


class Measurement:
    def __init__(self, args):
        self.args = args
        self.catalog = json.loads(args.catalog.read_text())
        self.suite = evaluation_suite()
        self.lock = threading.Lock()
        self.calls = 0
        self.client = boto3.client("bedrock-runtime", region_name=self.catalog["region"],
                                  config=Config(connect_timeout=5, read_timeout=120,
                                                retries={"max_attempts": 0}, max_pool_connections=16))
        args.output.mkdir(parents=True, exist_ok=True)
        manifest = {"catalog_version": self.catalog["version"], "suite": self.suite,
                    "configuration": {"inference_config": {"maxTokens": 4096}, "request_fields": {},
                                      "sampling": "provider defaults", "cache": False},
                    "scope": "visible development tasks; not held-out business success",
                    "catalog": self.catalog}
        path = args.output / "manifest.json"
        if path.exists() and json.loads(path.read_text()) != manifest:
            raise ValueError("Resume requires an identical catalog, suite and request configuration")
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    def record(self, model, case_id, workflow, call, judge=None):
        original_case_id = case_id
        cap = output_limit(model, 4096)
        if cap != 4096:
            case_id += "-bounded"
        path = self.args.output / "observations" / (
            hashlib.sha256(model["id"].encode()).hexdigest()[:16] + "-" + case_id + ".json")
        path.parent.mkdir(exist_ok=True)
        if path.exists():
            return json.loads(path.read_text())
        started = time.perf_counter()
        row = {"model_id": model["id"], "invoke_id": model["invoke_id"], "case_id": case_id,
               "workflow": workflow, "suite_version": self.suite["version"],
               "configuration": {"max_output_tokens": cap},
               "measured_at": datetime.now(UTC).isoformat()}
        if case_id != original_case_id:
            row["supersedes_case_id"] = original_case_id
        try:
            output = call()
            row.update(status="completed", output=output)
        except Exception as exc:
            error = exc.response["Error"] if isinstance(exc, ClientError) else {
                "Code": type(exc).__name__, "Message": str(exc)}
            row.update(status="error", error={"code": error["Code"], "message": error.get("Message", "")[:1500]})
        row["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        row["grade"] = judge(row["output"]) if row["status"] == "completed" and judge else {
            "status": "fail" if row["status"] == "error" else "pending",
            "source": "call_error" if row["status"] == "error" else "quality_unmeasured"}
        path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n")
        with self.lock:
            self.calls += 1
            if self.calls % 20 == 0:
                print(json.dumps({"new_observations": self.calls, "model": model["id"],
                                  "case": case_id, "status": row["status"]}), flush=True)
        return row

    def converse(self, model, text, attachments=()):
        content = [{"text": text}]
        content.extend(attachments)
        return response_output(self.client.converse(
            modelId=model["invoke_id"], messages=[{"role": "user", "content": content}],
            inferenceConfig={"maxTokens": output_limit(model, 4096)}))

    def tools(self, model):
        tool = {"toolSpec": {"name": "lookup_order", "description": "Read a supplied order record.",
                            "inputSchema": {"json": {"type": "object", "properties": {
                                "order_id": {"type": "string"}}, "required": ["order_id"]}}}}
        messages = [{"role": "user", "content": [{"text":
            'Use lookup_order with order_id "ORDER-7" to get its amount. Never invent an amount. '
            'After the tool responds, output just the amount as a number.'}]}]
        first = self.client.converse(modelId=model["invoke_id"], messages=messages,
                                     toolConfig={"tools": [tool]}, inferenceConfig={"maxTokens": output_limit(model, 4096)})
        message = first["output"]["message"]
        uses = [b["toolUse"] for b in message["content"] if "toolUse" in b]
        if len(uses) != 1 or uses[0].get("name") != "lookup_order" or uses[0].get("input") != {"order_id": "ORDER-7"}:
            return {"complete": False, "protocol_supported": bool(uses), "text": "",
                    "stop_reason": first.get("stopReason"), "usage": first.get("usage"),
                    "observed_tool_calls": uses}
        # Match the gateway's portable protocol, excluding provider-specific reasoning blocks.
        portable = {"role": "assistant", "content": [
            {"text": b["text"]} if "text" in b else {"toolUse": {
                k: b["toolUse"][k] for k in ("toolUseId", "name", "input")}}
            for b in message["content"] if "text" in b or "toolUse" in b]}
        messages.extend([portable, {"role": "user", "content": [{"toolResult": {
            "toolUseId": uses[0]["toolUseId"], "content": [{"json": {"amount": 129}}],
            "status": "success"}}]}])
        final = self.client.converse(modelId=model["invoke_id"], messages=messages,
                                     toolConfig={"tools": [tool]}, inferenceConfig={"maxTokens": output_limit(model, 4096)})
        result = response_output(final)
        result.update(protocol_supported=True, native_messages_supported=True,
                      first_usage=first.get("usage"), actual_model_calls=2)
        return result

    def measure(self, model):
        blocked = ("账户可用性未确认" if not model["account_available"] else
                   "需要预置容量或部署" if not model["invoke_id"] else
                   quota_exclusion(model, self.catalog["region"]))
        summary = {"model_id": model["id"], "invoke_id": model["invoke_id"], "observations": [],
                   "status": "blocked" if blocked else "unmeasured", "reason": blocked}
        if blocked:
            return summary
        mid = model["id"]
        text_model = ("TEXT" in model["input_modalities"] and "TEXT" in model["output_modalities"]
                      and not any(k in mid for k in ("rerank", "sonic", "pegasus", "safeguard")))
        if text_model:
            cases = [c for c in self.suite["cases"] if c["workflow"] in ("general_text", "reasoning", "coding")]
            for case in cases:
                judge = (lambda out, case=case: code_grade(case, out, self.args.code_image)) if case["workflow"] == "coding" else (
                    lambda out, case=case: grade(case, out))
                row = self.record(model, case["id"], case["workflow"],
                                  lambda case=case: self.converse(model, case["task"]["text"]), judge)
                summary["observations"].append(row)
                if row["status"] == "error" and row["error"]["code"] in {
                    "ValidationException", "AccessDeniedException", "ResourceNotFoundException"}:
                    summary.update(reason=row["error"], status="unsupported")
                    break
            if summary["status"] != "unsupported":
                summary["observations"].append(self.record(model, "portable-tools", "tools",
                    lambda: self.tools(model),
                    lambda output: grade({"judge": {"kind": "number", "expected": 129}}, output)))
                # A two-message history tests role preservation independently of tool support.
                def history():
                    response = self.client.converse(modelId=model["invoke_id"], messages=[
                        {"role": "user", "content": [{"text": "My project code is CEDAR-731."}]},
                        {"role": "assistant", "content": [{"text": "Noted."}]},
                        {"role": "user", "content": [{"text": "Reply with my project code only."}]},
                    ], inferenceConfig={"maxTokens": output_limit(model, 4096)})
                    return response_output(response)
                summary["observations"].append(self.record(model, "conversation", "multi_turn", history,
                    lambda output: grade({"judge": {"kind": "exact", "expected": "CEDAR-731"}}, output)))
        if "IMAGE" in model["input_modalities"] and "TEXT" in model["output_modalities"] and "pegasus" not in mid:
            for case in [c for c in self.suite["cases"] if c["workflow"] == "vision"]:
                raw = (ROOT / "app/evaluation_assets" / case["fixture"]).read_bytes()
                row = self.record(model, case["id"], "vision",
                                  lambda case=case, raw=raw: self.converse(model, case["task"]["text"], [
                                      {"image": {"format": "png", "source": {"bytes": raw}}}]),
                                  lambda output, case=case: grade(case, output))
                summary["observations"].append(row)
                if row["status"] == "error" and row["error"]["code"] in {"ValidationException", "AccessDeniedException"}:
                    break
        if summary["observations"] and summary["status"] != "unsupported":
            summary["status"] = "measured"
        if not summary["observations"]:
            summary["reason"] = "专用接口评测单独执行；不将文本分数用于此模态"
        return summary

    def run(self):
        models = self.catalog["models"]
        if self.args.model:
            models = [m for m in models if m["id"] in self.args.model]
        with ThreadPoolExecutor(max_workers=self.args.workers) as pool:
            results = list(pool.map(self.measure, models))
        (self.args.output / "results.json").write_text(json.dumps({
            "suite_version": self.suite["version"], "catalog_version": self.catalog["version"],
            "models": results}, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"models": len(results), "new_observations": self.calls,
                          "output": str(self.args.output)}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--code-image", required=True)
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    parser.add_argument("--model", action="append")
    args = parser.parse_args()
    Measurement(args).run()


if __name__ == "__main__":
    main()
