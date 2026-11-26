"""Independent content review of completed live AgentCore release checks."""

import argparse
import concurrent.futures
import hashlib
import json
import time
from pathlib import Path

import boto3
from botocore.config import Config

from app.industry_scenarios import SCENARIOS, system_prompt
from scripts.review_paired_quality import INSTRUCTIONS, review_tool
from scripts.review_profile_quality import MODEL, parse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args()
    output = args.input / "reviews"
    output.mkdir(exist_ok=True)
    client = boto3.client("bedrock-runtime", region_name="us-east-1",
                          config=Config(read_timeout=180, retries={"max_attempts": 0}))

    def review(path):
        job = json.loads(path.read_text())
        result = {"id": path.stem, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "judge_model": MODEL, "code_execution": False, "status": "started",
                  "instructions_sha256": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest()}
        dest = output / path.name
        dest.write_text(json.dumps(result))
        if job["status"] != "completed":
            result.update(status="completed", review={"status": "fail", "reason": "Agent执行未完成"})
        else:
            item = next(s for s in SCENARIOS if s["id"] == job["scenario_id"])
            data = {
                "task": job["prompt"], "authoritative_records": item["datasets"],
                "authoritative_policy": item["policy"], "context": item["context"],
                "system_instruction": system_prompt(item),
                "tool_results": [{k: e.get(k) for k in ("name", "arguments", "status", "result")}
                                 for e in job["events"] if e["type"] == "tool_result"],
                "answers": [{"id": "answer_1", "text": job["answer"]}],
            }
            result["input"] = data
            try:
                response = client.converse(modelId=MODEL, system=[{"text": INSTRUCTIONS}],
                    messages=[{"role": "user", "content": [{"text": json.dumps(data, ensure_ascii=False)}]}],
                    inferenceConfig={"maxTokens": 4096, "temperature": 0},
                    toolConfig={"tools": [review_tool({"answer_1": "current"})],
                                "toolChoice": {"tool": {"name": "submit_reviews"}}})
                result["raw_response"] = response
                tools = [b["toolUse"] for b in response["output"]["message"]["content"] if "toolUse" in b]
                if response.get("stopReason") != "tool_use" or len(tools) != 1 or tools[0]["name"] != "submit_reviews":
                    raise ValueError("Incomplete review")
                fields = ("status", "reason", "confidence", "code_review", "issues")
                values = {"id": "answer_1", **{f: tools[0]["input"]["answer_1_" + f] for f in fields}}
                result.update(status="completed", review=parse(json.dumps(values)))
            except Exception as exc:
                result.update(status="error", error=str(exc))
        dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(path.stem, result["status"], result.get("review", {}).get("status"), flush=True)

    deadline = time.monotonic() + 1800
    scenario_ids = {item["id"] for item in SCENARIOS}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
        while True:
            paths = [p for p in args.input.glob("*.json")
                     if p.stem.rpartition("-")[0] in scenario_ids and p.stem.rpartition("-")[2].isdigit()
                     and not (output / p.name).exists()
                     and json.loads(p.read_text()).get("status") != "running"]
            list(workers.map(review, paths))
            summary = args.input / "summary.json"
            done = summary.exists() and json.loads(summary.read_text()).get("finished_at")
            if done or not args.follow:
                break
            if time.monotonic() > deadline:
                raise TimeoutError("Agent review watcher reached limit; saved reviews are retained")
            time.sleep(10)
    client.close()


if __name__ == "__main__":
    main()
