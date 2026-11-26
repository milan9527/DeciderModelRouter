import copy
import json
from unittest.mock import MagicMock

import pytest

from app import routing
from app.routing_balance import RoutingPreferences
from app.routing_inventory import inventory
from app.routing_profile import RequestProfile
from app.routing_selector import configuration_id, load_calibration
from scripts.routing_benchmark_sources import BASE, REFERENCE_URL, compile_livebench


def profile(workflow="customer_support"):
    return RequestProfile(
        workflow=workflow,
        difficulty="moderate",
        stronger_model_benefit="medium",
        latency="normal",
        tools="no",
        structured_output="no",
        languages="zh",
    )


def selection(release="auto", **preferences):
    task = routing.Task(
        text="写一条客服答复",
        routing_preferences=RoutingPreferences(benchmark_release=release, **preferences),
    )
    return routing.auto_selection(
        task, profile(), routing.load_catalog(), routing.load_rankings(), load_calibration()
    )


def source_fixture():
    registry = {
        "releases": ["2025-01-01", "2026-01-01"],
        "models": {
            "exact-model": {
                "benchmark_ids": ["exact-source"],
                "reference_id": "provider/exact",
                "configuration_status": "verified",
                "request_fields": {},
            }
        },
    }
    files = {
        REFERENCE_URL: json.dumps(
            {
                "data": [
                    {
                        "id": "provider/exact",
                        "name": "Exact",
                        "context_length": 8192,
                        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                    }
                ]
            }
        ).encode(),
        BASE
        + "cost_2026_01_01.csv": b"model,input_price_per_million,output_price_per_million\nanother,0,0\n",
    }
    for release, score in [("2025_01_01", 40), ("2026_01_01", 80)]:
        files[BASE + f"table_{release}.csv"] = (
            f"model,code,lang,if\nexact-source,{score},50,60\n".encode()
        )
        files[BASE + f"categories_{release}.json"] = json.dumps(
            {"Coding": ["code"], "Language": ["lang"], "IF": ["if"]}
        ).encode()
    return registry, files


def compile_fixture(registry, files):
    evidence = []
    result = compile_livebench(
        {"livebench": {}, "livebench_request_fields": {}},
        registry,
        files.__getitem__,
        evidence,
        "test-snapshot",
    )
    assert all(len(row["sha256"]) == 64 for row in evidence)
    return result


def test_release_scores_and_price_provenance_are_not_merged():
    registry, files = source_fixture()
    entries, references, releases = compile_fixture(registry, files)
    row = entries["exact-model"]
    assert row["category_scores"]["Coding"] == 80
    assert row["configurations"][0]["category_scores"]["Coding"] == 40
    assert row["dataset_revision"] != row["configurations"][0]["dataset_revision"]
    assert row["metrics"]["input_price"] == 1
    assert row["metrics"]["output_price"] == 2
    assert row["pricing_source"]["source"] == "openrouter"
    assert references["exact-model"]["id"] == "provider/exact"
    assert len(releases) == 2


def test_missing_price_and_missing_category_remain_unknown():
    registry, files = source_fixture()
    files[REFERENCE_URL] = b'{"data":[]}'
    files[BASE + "table_2026_01_01.csv"] = b"model,code,lang,if\nexact-source,,50,60\n"
    row = compile_fixture(registry, files)[0]["exact-model"]
    assert row["category_scores"]["Coding"] is None
    assert row["category_scores"]["Language"] == 50
    assert row["metrics"]["quality"] is None
    assert row["metrics"]["input_price"] is None
    assert row["pricing_source"] is None


def test_missing_exact_identity_aborts_refresh_instead_of_borrowing_scores():
    registry, files = source_fixture()
    registry["models"]["exact-model"]["benchmark_ids"] = ["exact-source-newer"]
    with pytest.raises(ValueError, match="Unresolved exact benchmark"):
        compile_fixture(registry, files)


@pytest.mark.parametrize("bad", ["NaN", "101", "-1"])
def test_invalid_scores_abort_refresh(bad):
    registry, files = source_fixture()
    files[BASE + "table_2026_01_01.csv"] = (
        f"model,code,lang,if\nexact-source,{bad},50,60\n".encode()
    )
    with pytest.raises(ValueError):
        compile_fixture(registry, files)


def test_reference_identity_alone_is_not_a_benchmark_mapping():
    registry, files = source_fixture()
    registry["models"]["reference-only"] = {
        "benchmark_ids": [],
        "reference_id": "provider/exact",
    }
    entries, references, _ = compile_fixture(registry, files)
    assert "reference-only" in references and "reference-only" not in entries


def test_reference_alias_drift_aborts_instead_of_pricing_another_model_version():
    registry, files = source_fixture()
    registry["models"]["exact-model"]["reference_canonical_slug"] = "provider/exact-version-1"
    data = json.loads(files[REFERENCE_URL])
    data["data"][0]["canonical_slug"] = "provider/exact-version-2"
    files[REFERENCE_URL] = json.dumps(data).encode()
    with pytest.raises(ValueError, match="alias changed"):
        compile_fixture(registry, files)


def test_auto_and_explicit_release_never_mix_datasets():
    for release in ("auto", "2025-04-02", "2026-01-08", "2026-06-25"):
        result = selection(release)
        assert result["candidates"]
        revisions = {r["benchmark"]["dataset_revision"] for r in result["candidates"]}
        assert len(revisions) == 1
        if release != "auto":
            assert revisions == {f"livebench-{release}"}
        assert len({tuple(r["task_scores"]) for r in result["candidates"]}) == 1
    old = selection("2025-04-02")
    assert "amazon.nova-micro-v1:0" in {r["model"]["id"] for r in old["candidates"]}
    assert "google.gemma-3-4b-it" in {r["model"]["id"] for r in old["candidates"]}


def test_pending_configuration_cannot_be_forced_into_routing():
    result = selection(allowed_models=["anthropic.claude-fable-5"])
    assert result["selected_candidate_id"] is None
    assert "回退" in result["excluded"]["anthropic.claude-fable-5"]


def test_unknown_release_has_no_route_and_does_not_fall_back():
    result = selection("2099-01-01")
    assert result["selected_candidate_id"] is None
    assert not result["candidates"]


def test_configuration_identity_binds_release_and_sampling_parameters():
    row = routing.load_rankings()["entries"]["amazon.nova-micro-v1:0"]
    other = copy.deepcopy(row)
    other["dataset_revision"] = "another-release"
    assert configuration_id("model", row) != configuration_id("model", other)
    other = {**row, "inference_config": {"temperature": 0.6}}
    assert configuration_id("model", row) != configuration_id("model", other)


def test_execution_honors_mapped_sampling_and_user_output_cap():
    runtime = MagicMock()
    runtime.converse.return_value = {
        "output": {"message": {"content": [{"text": "completed"}]}},
        "stopReason": "end_turn",
    }
    bench = {"request_fields": {}, "inference_config": {"temperature": 1}}
    task = routing.Task(
        text="x", routing_preferences={"expected_output_tokens": 100, "max_output_tokens": 512}
    )
    result = routing.execute_target(
        runtime,
        {"id": "minimax.minimax-m2.5", "invoke_id": "minimax.minimax-m2.5"},
        task,
        "coding",
        bench,
    )
    assert result["complete"]
    assert runtime.converse.call_args.kwargs["inferenceConfig"] == {
        "temperature": 1,
        "maxTokens": 512,
    }


def test_every_catalog_model_has_an_explanation_without_aliasing_capacity_variants():
    result = inventory(routing.load_catalog(), routing.load_rankings())
    assert result["total_models"] == 128 and result["mapped_models"] == 60
    assert sum(result["counts"].values()) == 128
    nova = result["models"]["amazon.nova-micro-v1:0"]
    assert nova["status"] == "benchmarked" and nova["configurations"]
    variant = result["models"]["amazon.nova-micro-v1:0:128k"]
    assert variant["status"] == "capacity_variant" and not variant["configurations"]
    qwen = result["models"]["qwen.qwen3-coder-next"]
    assert qwen["status"] == "reference_only" and not qwen["configurations"]
    assert all(row["reason"] for row in result["models"].values())
