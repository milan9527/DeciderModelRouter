"""AWS snapshot rates for the selected endpoint, context length and cache usage."""

import math
from datetime import UTC, datetime
from decimal import Decimal


def current(pricing, now=None):
    until = pricing.get("valid_until")
    return not until or (now or datetime.now(UTC)) < datetime.fromisoformat(until.replace("Z", "+00:00"))


def rates_for(pricing, input_tokens):
    if not current(pricing):
        return {}
    threshold = pricing.get("long_context_threshold")
    tier = "long_context" if threshold is not None and input_tokens > threshold else "standard"
    return pricing.get("rates", {}).get(tier, {}) if pricing.get("rates") else pricing


def token_cost(rates, counts):
    total = Decimal(0)
    for key, count in counts.items():
        if type(count) is not int or count < 0:
            return None
        if count == 0:
            continue
        price = rates.get(key)
        if type(price) not in (float, int) or not math.isfinite(price) or price < 0:
            return None
        total += Decimal(str(price)) * count / 1_000_000
    return total


def observed_cost(benchmark, usage, workflow):
    pricing = benchmark.get("pricing_source")
    if not pricing or not pricing.get("rates") or not usage:
        return None
    # These modalities can incur non-text meters not present in token usage.
    token_audio = workflow == "audio_understanding" and pricing.get("invoke_id", "").startswith("mistral.voxtral-")
    if workflow not in {"general_text", "customer_support", "reasoning", "coding", "embedding", "vision"} and not token_audio:
        return None
    inputs = usage.get("inputTokens", usage.get("input_tokens"))
    outputs = 0 if workflow == "embedding" else usage.get("outputTokens", usage.get("output_tokens"))
    read = usage.get("cacheReadInputTokens", 0)
    write = usage.get("cacheWriteInputTokens", 0)
    if any(type(v) is not int or v < 0 for v in (inputs, outputs, read, write)):
        return None
    # Converse inputTokens excludes cache reads and writes. Threshold uses the whole prompt.
    rates = rates_for(pricing, inputs + read + write)
    counts = {"input_price": inputs, "output_price": outputs, "cache_read_price": read}
    if write:
        # Current explicit checkpoints use the default TTL; alternative TTL tariffs stay separate.
        ttl = pricing.get("default_cache_write_ttl_seconds", 300)
        counts[f"cache_write_price_{ttl}"] = write
    return token_cost(rates, counts)
