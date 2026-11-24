"""Explicit AWS quota bindings; never borrow Mantle/global quotas for a US Converse endpoint."""

import copy
import hashlib
import json
import math
from datetime import UTC, datetime

# Verified with Service Quotas in us-east-1, 2026-11-24. Keys are invoke IDs, not
# display-name substrings: K3.1 is distinct from K3, as are regional/global/Mantle.
# Unknown bindings remain unknown. These are quota identifiers, not quota values.
QUOTA_BINDINGS = {
    "amazon.nova-micro-v1:0": ("L-CFA4FA0D", "L-E118F160"),
    "amazon.nova-lite-v1:0": ("L-70423BF8", "L-E386A278"),
    "amazon.nova-pro-v1:0": ("L-CE33604C", "L-F2717A44"),
    "us.anthropic.claude-fable-5-1": ("L-E8940935",),
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": ("L-58BE175A", "L-CCA5DF70"),
    "us.anthropic.claude-opus-4-5-20251101-v1:0": ("L-7007E9C9",),
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": ("L-F4DDD3EB", "L-4A6BFAB1"),
    "us.deepseek.r1-v1:0": ("L-06B03968",),
    "google.gemma-3-4b-it": ("L-73FB8466", "L-3056DF33"),
    "google.gemma-3-12b-it": ("L-3FD4A73E", "L-999037CA"),
    "google.gemma-3-27b-it": ("L-F8729E94", "L-5D46C7AF"),
    "us.meta.llama3-1-8b-instruct-v1:0": ("L-9782749C", "L-396C5302"),
    "us.meta.llama3-1-70b-instruct-v1:0": ("L-92E68994", "L-29644EB3"),
    "us.meta.llama3-3-70b-instruct-v1:0": ("L-0E7AA8B7", "L-DEDE703C"),
    "meta.llama3-8b-instruct-v1:0": ("L-03A9B835", "L-320BEFEB"),
    "meta.llama3-70b-instruct-v1:0": ("L-609E24B0", "L-46D383AF"),
    "us.meta.llama4-maverick-17b-instruct-v1:0": ("L-DE3FBBF4",),
    "minimax.minimax-m2": ("L-A81B7C40", "L-828C986E"),
    "minimax.minimax-m2.5": ("L-359D5670", "L-6185F03F"),
    "mistral.devstral-2-123b": ("L-904A6762", "L-0197660A"),
    "mistral.mistral-7b-instruct-v0:2": ("L-02D831F1",),
    "mistral.mistral-large-2402-v1:0": ("L-01447289",),
    "mistral.mistral-small-2402-v1:0": ("L-82C15FA8",),
    "mistral.mixtral-8x7b-instruct-v0:1": ("L-490F4D1F",),
    "qwen.qwen3-next-80b-a3b": ("L-37AB702E",),
    "us.anthropic.claude-haiku-5-5": ("L-47520EB7",),
    "us.anthropic.claude-opus-4-6-v1": ("L-0AD9BBE8",),
    "us.anthropic.claude-opus-4-7": ("L-5DB28B7B",),
    "us.anthropic.claude-opus-4-8": ("L-DB99DCDB",),
    "us.anthropic.claude-opus-5": ("L-99296DCD",),
    "us.anthropic.claude-opus-5-5": ("L-A4430697",),
    "us.anthropic.claude-sonnet-4-6": ("L-15B8E632",),
    "us.anthropic.claude-sonnet-5": ("L-D4FBCF4E",),
    "us.anthropic.claude-sonnet-5-5": ("L-94A31E46",),
    "cohere.embed-english-v3": ("L-A2BE277A", "L-FF8E7864"),
    "cohere.embed-multilingual-v3": ("L-C2F86908", "L-9E5BD0C6"),
    "cohere.embed-v4:0": ("L-C47B85D5", "L-BE5FD99B"),
    "us.moonshotai.kimi-k3": ("L-51FF35D5",),
    "global.moonshotai.kimi-k3": ("L-2B5A3FA2",),
    "us.moonshotai.kimi-k3.1": ("L-B37A8021",),
    "global.moonshotai.kimi-k3.1": ("L-8D6762F0",),
    "us.openai.gpt-5.6-luna": ("L-E3C70889",),
    "us.openai.gpt-5.6-sol": ("L-D94ECE3B",),
    "us.openai.gpt-5.6-terra": ("L-99936FBF",),
    "us.openai.gpt-6-astra": ("L-53A144CD",),
    "us.openai.gpt-6-luna": ("L-AB39D760",),
    "us.openai.gpt-6-sol": ("L-8FD128CF",),
    "us.openai.gpt-6.1-sol": ("L-8C5F762B",),
    "us.zai.glm-5.3": ("L-4A544964",),
    "global.zai.glm-5.3": ("L-6473F1D2",),
    "us.xai.grok-4.6": ("L-0F2ECB41",),
    "us.xai.grok-4.7": ("L-5DAEA81C",),
}


def apply_quota_snapshot(catalog, client):
    region = catalog["region"]
    if client.meta.region_name != region:
        raise ValueError("Quota client and catalog regions differ")
    # These explicit bindings have been verified only in the deployed region.
    if region != "us-east-1":
        raise ValueError("Verify quota bindings for this region before collecting evidence")
    quotas = {}
    for page in client.get_paginator("list_service_quotas").paginate(ServiceCode="bedrock"):
        for quota in page["Quotas"]:
            quotas[quota["QuotaCode"]] = quota
    data = copy.deepcopy(catalog)
    checked_at = datetime.now(UTC).isoformat()
    checked = 0
    for model in data["models"]:
        codes = QUOTA_BINDINGS.get(model["invoke_id"])
        if not codes:
            if "inference_quotas" in model:
                raise ValueError("Existing quota evidence has no binding; refusing to erase it")
            continue
        checks = []
        for code in codes:
            quota = quotas.get(code)
            if quota is None:
                raise ValueError(f"Missing applied quota {code}; catalog was not updated")
            value = quota.get("Value")
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid applied quota value for {code}")
            checks.append({"code": code, "name": quota["QuotaName"], "value": value})
        model["inference_quotas"] = {
            "region": region,
            "invoke_id": model["invoke_id"],
            "checked_at": checked_at,
            "source": "AWS Service Quotas ListServiceQuotas (applied account values)",
            "checks": checks,
        }
        checked += 1
    data["quota_snapshot"] = {
        "checked_at": checked_at,
        "checked_models": checked,
        "unmapped_models": len(data["models"]) - checked,
        "note": (
            "Exact invocation-endpoint quota snapshot, not live remaining capacity. "
            "Zero quotas stay excluded until refreshed; positive/unknown quotas do not "
            "guarantee runtime capacity. No Mantle quota is used for Converse."
        ),
    }
    content = {"region": region, "models": data["models"]}
    data["version"] = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]
    return data
