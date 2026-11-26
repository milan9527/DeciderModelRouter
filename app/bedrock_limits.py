"""Output ceilings verified against the exact Bedrock invocation endpoint."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache
def _limits():
    path = Path(__file__).with_name("bedrock_limits.json")
    return json.loads(path.read_text())["entries"] if path.exists() else {}


def model_limits(model):
    entry = _limits().get(model.get("id"), {})
    return entry if entry.get("invoke_id") == model.get("invoke_id") else {}


def output_limit(model, requested):
    ceiling = model_limits(model).get("output_token_ceiling")
    return min(requested, ceiling) if isinstance(ceiling, int) else requested


def output_budget(model, requested):
    return {"requested_max_tokens": requested, "effective_max_tokens": output_limit(model, requested),
            "source": "exact_endpoint_parameter_probe" if model_limits(model) else "caller_limit_unverified",
            "note": "不超过请求上限；已核验的端点限制可能更低。"}
