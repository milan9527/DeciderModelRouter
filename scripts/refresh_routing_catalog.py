"""Snapshot every regional Bedrock foundation model and its account availability."""

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

if __package__:
    from scripts.routing_quota_snapshot import apply_quota_snapshot
else:
    from routing_quota_snapshot import apply_quota_snapshot


def refresh_quotas(data):
    client = boto3.client(
        "service-quotas",
        region_name=data["region"],
        config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}),
    )
    try:
        return apply_quota_snapshot(data, client)
    finally:
        client.close()


def collect(region):
    client = boto3.client(
        "bedrock",
        region_name=region,
        config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}),
    )
    models = client.list_foundation_models()["modelSummaries"]
    profiles, kwargs = [], {}
    while True:
        page = client.list_inference_profiles(**kwargs)
        profiles.extend(page["inferenceProfileSummaries"])
        if not page.get("nextToken"):
            break
        kwargs["nextToken"] = page["nextToken"]

    def normalize(model):
        mid = model["modelId"]
        matches = [
            p
            for p in profiles
            if p["status"] == "ACTIVE"
            and p.get("type") == "SYSTEM_DEFINED"
            and any(m["modelArn"].split("foundation-model/")[-1] == mid for m in p["models"])
        ]
        matches.sort(
            key=lambda p: (not p["inferenceProfileId"].startswith("us."), p["inferenceProfileId"])
        )
        direct = "ON_DEMAND" in model["inferenceTypesSupported"]
        profile = matches[0] if matches else None
        try:
            availability = client.get_foundation_model_availability(modelId=mid)
            availability.pop("ResponseMetadata", None)
        except (ClientError, BotoCoreError) as exc:
            availability = {"status": "UNKNOWN", "error_type": type(exc).__name__}
        available = (
            availability.get("regionAvailability") == "AVAILABLE"
            and availability.get("authorizationStatus") == "AUTHORIZED"
            and availability.get("entitlementAvailability") == "AVAILABLE"
            and availability.get("agreementAvailability", {}).get("status") == "AVAILABLE"
        )
        return {
            "id": mid,
            "name": model["modelName"],
            "provider": model["providerName"],
            "input_modalities": model["inputModalities"],
            "output_modalities": model["outputModalities"],
            "inference_types": model["inferenceTypesSupported"],
            "lifecycle": model.get("modelLifecycle", {}).get("status", "UNKNOWN"),
            "streaming": model.get("responseStreamingSupported", False),
            "availability": availability,
            "account_available": available,
            "invoke_id": mid if direct else profile["inferenceProfileId"] if profile else None,
            "invocation": "regional"
            if direct
            else "inference_profile"
            if profile
            else "requires_capacity",
            "destination_regions": [region]
            if direct
            else sorted({m["modelArn"].split(":")[3] or "global" for m in profile["models"]})
            if profile
            else [],
        }

    with ThreadPoolExecutor(max_workers=6) as executor:
        rows = sorted(executor.map(normalize, models), key=lambda m: (m["provider"], m["id"]))
    client.close()
    content = {"region": region, "models": rows}
    return refresh_quotas(
        {
            **content,
            "captured_at": datetime.now(UTC).isoformat(),
            "version": hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[
                :16
            ],
            "source": "Bedrock ListFoundationModels + ListInferenceProfiles + GetFoundationModelAvailability",
            "note": "Availability and mapped quotas are dated account/region snapshots, not live capacity guarantees.",
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--output", type=Path, default=Path("app/bedrock_catalog.json"))
    parser.add_argument("--quotas-only", action="store_true", help="Keep existing model metadata")
    parser.add_argument("--input", type=Path, help="Existing catalog for --quotas-only")
    args = parser.parse_args()
    if args.input and not args.quotas_only:
        parser.error("--input requires --quotas-only")
    if args.quotas_only:
        current = json.loads((args.input or args.output).read_text())
        if current["region"] != args.region:
            parser.error("Input catalog and requested regions differ")
        data = refresh_quotas(current)
    else:
        data = collect(args.region)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "region": data["region"],
                "models": len(data["models"]),
                "account_available": sum(m["account_available"] for m in data["models"]),
                "version": data["version"],
                "quota_snapshot": data["quota_snapshot"],
            }
        )
    )


if __name__ == "__main__":
    main()
