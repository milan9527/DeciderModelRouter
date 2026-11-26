import copy
from datetime import UTC, datetime
from decimal import Decimal

from app.bedrock_evidence import load_evidence
from app.bedrock_pricing import current, observed_cost, rates_for
from app.routing_balance import observed_reference_cost
from scripts.refresh_bedrock_prices import compile_price


def price(mid):
    return load_evidence()["entries"][mid]["pricing"]


def test_exact_aws_runtime_prices_and_boundary():
    astra = price("openai.gpt-6.2-astra")
    assert astra["inference_scope"] == "us_cross_region"
    assert rates_for(astra, 272000)["input_price"] == 8.8
    assert rates_for(astra, 272001)["input_price"] == 17.6
    assert rates_for(astra, 272001)["output_price"] == 66
    haiku = price("anthropic.claude-haiku-5-5")
    assert haiku["long_context_threshold"] == 100000
    assert rates_for(haiku, 100001) == haiku["rates"]["long_context"]


def test_cache_tokens_are_billed_separately_and_count_toward_context_threshold():
    bench = {"pricing_source": price("openai.gpt-6.2-astra")}
    usage = {"inputTokens": 1000, "outputTokens": 200, "cacheReadInputTokens": 3000,
             "cacheWriteInputTokens": 100}
    assert observed_cost(bench, usage, "reasoning") == Decimal("0.02002")
    usage = {"inputTokens": 272000, "outputTokens": 1, "cacheReadInputTokens": 1}
    assert observed_cost(bench, usage, "reasoning") == Decimal("4.78726688")


def test_missing_cache_tariff_is_unknown_not_free_and_expiry_is_enforced():
    p = copy.deepcopy(price("openai.gpt-6.2-astra"))
    p["rates"]["standard"].pop("cache_read_price")
    assert observed_cost({"pricing_source": p}, {"inputTokens": 100, "outputTokens": 5,
                                               "cacheReadInputTokens": 1}, "coding") is None
    assert current({"valid_until": "2026-12-01T00:00:00Z"}, datetime(2026, 11, 30, tzinfo=UTC))
    assert not current({"valid_until": "2026-12-01T00:00:00Z"}, datetime(2026, 12, 1, tzinfo=UTC))
    p["valid_until"] = "2000-01-01T00:00:00Z"
    assert rates_for(p, 1) == {}


def test_input_only_embedding_does_not_require_output_price():
    p = price("cohere.embed-english-v3")
    assert p["input_price"] == 0.1 and "output_price" not in p
    assert observed_reference_cost({"pricing_source": p}, {"input_tokens": 50}, "embedding") == "0.000005"


def test_unknown_units_and_non_token_media_are_not_given_a_token_quote():
    p = price("twelvelabs.marengo-embed-3-0-v1:0")
    assert "input_price" not in p
    assert observed_cost({"pricing_source": p}, {"inputTokens": 100}, "embedding") is None
    assert observed_cost({"pricing_source": price("openai.gpt-6.2-astra")},
                         {"inputTokens": 100, "outputTokens": 1}, "video_understanding") is None
    voxtral = price("mistral.voxtral-mini-3b-2507")
    assert observed_cost({"pricing_source": voxtral},
                         {"input_tokens": 386, "output_tokens": 9}, "audio_understanding") is not None


def test_compiler_separates_global_standard_batch_and_units():
    products, terms = {}, {}
    for sku, suffix, amount, unit in [
        ("us-input", "input_tokens_standard", "0.0088", "1K tokens"),
        ("us-output", "output_tokens_standard", "44", "1M tokens"),
        ("global", "input_tokens_global_standard", "8", "1M tokens"),
        ("batch", "input_tokens_batch", "4.4", "1M tokens"),
        ("fast", "input_tokens_ultrafast", "26.4", "1M tokens"),
    ]:
        products[sku] = {"attributes": {"model": "OpenAI GPT-6.2 Astra", "regionCode": "us-east-1",
                                       "feature": "On-demand Inference", "usagetype": suffix,
                                       "inferenceType": "Output tokens" if sku == "us-output" else "Input tokens"}}
        terms[sku] = {"term": {"priceDimensions": {"rate": {
            "rateCode": sku, "beginRange": "0", "endRange": "Inf", "unit": unit,
            "pricePerUnit": {"USD": amount}}}}}
    document = {"products": products, "terms": {"OnDemand": terms}, "offerCode": "AmazonBedrockFoundationModels",
                "_source": "https://pricing.us-east-1.amazonaws.com/offers/test", "publicationDate": "2026-11-23"}
    model = {"id": "openai.gpt-6.2-astra", "name": "GPT-6.2 Astra", "invoke_id": "us.openai.gpt-6.2-astra"}
    compiled = compile_price(model, [document], "us-east-1")
    assert compiled["input_price"] == 8.8 and compiled["output_price"] == 44
    assert compiled["skus"] == ["us-input", "us-output"]
    model["invoke_id"] = "global.openai.gpt-6.2-astra"
    assert compile_price(model, [document], "us-east-1")["input_price"] == 8
