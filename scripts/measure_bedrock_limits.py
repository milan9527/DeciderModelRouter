"""Probe output parameter acceptance; this measures API limits, not answer quality."""

import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.routing import load_catalog
from app.routing_quota import quota_exclusion


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog()
    client = boto3.client("bedrock-runtime", region_name=catalog["region"],
                          config=Config(read_timeout=120, retries={"max_attempts": 0},
                                        max_pool_connections=8))

    def measure(model):
        path = args.output / (hashlib.sha256(model["id"].encode()).hexdigest()[:16] + ".json")
        if path.exists():
            return json.loads(path.read_text())
        entry = {"model_id": model["id"], "invoke_id": model["invoke_id"],
                 "measured_at": datetime.now(UTC).isoformat(), "attempts": []}
        cap = 16384
        for _ in range(2):
            attempt = {"requested_max_tokens": cap}
            try:
                response = client.converse(modelId=model["invoke_id"],
                    messages=[{"role": "user", "content": [{"text": "Reply only OK."}]}],
                    inferenceConfig={"maxTokens": cap})
                attempt.update(status="accepted", usage=response.get("usage"),
                               request_id=response.get("ResponseMetadata", {}).get("RequestId"))
                entry["accepted_output_limit"] = cap
            except ClientError as exc:
                error = exc.response["Error"]
                attempt.update(status="error", error=error)
                if error["Code"] == "ValidationException":
                    limit = re.search(r"exceeds the model limit of (\d+)", error["Message"])
                    context = re.search(r"maximum context length is (\d+) tokens", error["Message"])
                    if limit:
                        entry["output_token_ceiling"] = int(limit[1])
                        entry["ceiling_kind"] = "provider_reported_maximum"
                    elif context:
                        entry["context_tokens"] = int(context[1])
                        entry["output_token_ceiling"] = int(context[1]) // 2
                        entry["ceiling_kind"] = "conservative_half_context"
            entry["attempts"].append(attempt)
            if attempt["status"] == "accepted" or "output_token_ceiling" not in entry or cap == entry["output_token_ceiling"]:
                break
            cap = entry["output_token_ceiling"]
        path.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n")
        return entry

    models = [m for m in catalog["models"] if m["invoke_id"] and m["account_available"]
              and not quota_exclusion(m, catalog["region"])
              and "TEXT" in m["input_modalities"] and "TEXT" in m["output_modalities"]
              and not any(part in m["id"] for part in ("sonic", "pegasus", "safeguard", "rerank"))]
    with ThreadPoolExecutor(max_workers=6) as executor:
        entries = {row["model_id"]: row for row in executor.map(measure, models)}
    data = {"catalog_version": catalog["version"], "scope": "parameter acceptance, not quality",
            "entries": entries}
    Path("app/bedrock_limits.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"models": len(entries), "bounded": sum("output_token_ceiling" in e for e in entries.values()),
                      "accepted": sum("accepted_output_limit" in e for e in entries.values())}))


if __name__ == "__main__":
    main()
