import copy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import routing
from app.routing_profile import RequestProfile
from app.routing_quota import quota_exclusion
from app.routing_selector import load_calibration
from scripts.routing_quota_snapshot import apply_quota_snapshot


def model():
    return {
        "id": "moonshotai.kimi-k3",
        "invoke_id": "us.moonshotai.kimi-k3",
        "inference_quotas": {
            "region": "us-east-1",
            "invoke_id": "us.moonshotai.kimi-k3",
            "checked_at": "2026-11-24T12:00:00+00:00",
            "checks": [{"code": "L-51FF35D5", "name": "Kimi K3 TPM", "value": 0}],
        },
    }


def quota_client(value):
    client = MagicMock()
    client.meta = SimpleNamespace(region_name="us-east-1")
    client.get_paginator.return_value.paginate.return_value = [
        {
            "Quotas": [
                {"QuotaCode": "L-51FF35D5", "QuotaName": "Kimi K3 TPM", "Value": value},
                # A positive global quota must never override a zero US quota.
                {"QuotaCode": "L-2B5A3FA2", "QuotaName": "Global Kimi K3 TPM", "Value": 10000},
            ]
        }
    ]
    return client


def test_zero_quota_excluded_even_though_account_is_authorized():
    catalog = routing.load_catalog()
    kimi = next(m for m in catalog["models"] if m["id"] == "moonshotai.kimi-k3")
    kimi["account_available"] = True
    kimi["inference_quotas"] = model()["inference_quotas"]
    assert "配额为 0" in routing.exclusion(kimi, routing.Task(text="write code"), "coding")
    task = routing.Task(
        text="write code",
        routing_preferences={"allowed_models": ["moonshotai.kimi-k3"]},
    )
    profile = RequestProfile(
        workflow="coding",
        difficulty="moderate",
        stronger_model_benefit="low",
        latency="normal",
        tools="no",
        structured_output="no",
        languages="en_zh",
    )
    selected = routing.auto_selection(
        task, profile, catalog, routing.load_rankings(), load_calibration()
    )
    assert selected["selected_candidate_id"] is None
    assert "配额为 0" in selected["excluded"]["moonshotai.kimi-k3"]


def test_quota_refresh_is_endpoint_bound_and_reenables_after_real_increase():
    original = {"region": "us-east-1", "models": [model()], "version": "old"}
    saved = copy.deepcopy(original)
    zero = apply_quota_snapshot(original, quota_client(0))
    assert zero["version"] != "old"
    assert "配额为 0" in quota_exclusion(zero["models"][0], "us-east-1")
    positive = apply_quota_snapshot(zero, quota_client(10000))
    assert positive["version"] != zero["version"]
    assert quota_exclusion(positive["models"][0], "us-east-1") is None
    assert original == saved


@pytest.mark.parametrize("value", [None, True, -1, float("nan"), float("inf")])
def test_invalid_quota_cannot_be_published_or_used(value):
    original = {"region": "us-east-1", "models": [model()]}
    with pytest.raises(ValueError, match="Invalid applied quota"):
        apply_quota_snapshot(original, quota_client(value))
    broken = model()
    broken["inference_quotas"]["checks"][0]["value"] = value
    assert "无效" in quota_exclusion(broken, "us-east-1")


def test_unknown_is_not_zero_and_mismatched_endpoint_is_not_capacity():
    unknown = {"id": "unmapped", "invoke_id": "us.unmapped"}
    assert quota_exclusion(unknown, "us-east-1") is None
    mismatched = model()
    mismatched["invoke_id"] = "global.moonshotai.kimi-k3"
    assert "不一致" in quota_exclusion(mismatched, "us-east-1")
    assert "不一致" in quota_exclusion(model(), "us-west-2")
    catalog = {"region": "us-east-1", "models": [unknown]}
    snapshot = apply_quota_snapshot(catalog, quota_client(0))
    assert snapshot["quota_snapshot"]["unmapped_models"] == 1
    assert "inference_quotas" not in snapshot["models"][0]


def test_failed_refresh_does_not_erase_previous_zero_quota():
    catalog = {"region": "us-east-1", "models": [model()]}
    client = quota_client(0)
    client.get_paginator.return_value.paginate.return_value = [{"Quotas": []}]
    with pytest.raises(ValueError, match="Missing applied quota"):
        apply_quota_snapshot(catalog, client)
    assert "配额为 0" in quota_exclusion(catalog["models"][0], "us-east-1")
    client.meta.region_name = "us-west-2"
    with pytest.raises(ValueError, match="regions differ"):
        apply_quota_snapshot(catalog, client)
