import copy
import json

from app.gateway_evidence_input import primary_evidence, selection_payload
from app.gateway_model_pool import build_pool
from app.routing import Task, exclusion, load_catalog, load_rankings
from app.routing_profile import replay_profile
from deployment.open_models.gateway import validate


def sample():
    task = Task(text="分析产线混杂因素", routing_preferences={"cost_tier": "high"})
    profile = replay_profile(task, "reasoning")
    pool = build_pool(task, profile, load_catalog(), load_rankings(), exclusion)
    aliases = {f"m{i:03d}": {**r, "workflow": "reasoning"} for i, r in enumerate(pool["candidates"])}
    return task, profile, aliases


def test_names_and_model_identifiers_cannot_change_anonymous_gpu_input():
    task, profile, aliases = sample()
    state = {"request": task.text, "task_profile": profile.model_dump()}
    first = selection_payload(task, state, aliases, None)
    changed = copy.deepcopy(aliases)
    for row in changed.values():
        row["model"].update(id="MISLEADING-MODEL", name="MISLEADING-MODEL", provider="MISLEADING-MODEL")
        for record in row["public_benchmarks"]:
            record.update(source_model_id="MISLEADING-MODEL", configuration="MISLEADING-MODEL")
    # Retain the effort descriptor for records whose source does not publish explicit fields.
    for k, row in changed.items():
        for original, updated in zip(aliases[k]["public_benchmarks"], row["public_benchmarks"], strict=True):
            if original["configuration"] == "highest_supported_effort":
                updated["configuration"] = original["configuration"]
    assert selection_payload(task, state, changed, None) == first
    assert len(first["questions"]["action"]["criteria"]) == len(aliases)
    validate(first)


def test_summary_uses_one_complete_batch_and_is_not_an_eligibility_filter():
    task, profile, aliases = sample()
    primary, scores = primary_evidence(aliases)
    assert primary["release"] == "2026-11-06"
    assert set(primary["dimensions"]) == {"Reasoning", "Data Analysis"}
    dev = next(k for k, row in aliases.items() if row["model"]["id"] == "mistral.devstral-2-123b")
    sol = next(k for k, row in aliases.items() if row["model"]["id"] == "openai.gpt-6.2-sol")
    assert scores[dev]["mean"] is None
    assert scores[sol]["mean"] == 85.85
    payload = selection_payload(task, {"request": task.text, "task_profile": profile.model_dump()}, aliases, None)
    assert dev in payload["questions"]["action"]["criteria"]
    assert "Reasoning=27.74" in json.loads(payload["state"])["candidates"][dev]
    assert "default" in json.loads(payload["state"])["candidates"][sol]
    assert primary_evidence(aliases, "2026-01-08")[0]["release"] == "2026-01-08"


def test_incomplete_primary_dimensions_do_not_receive_a_partial_mean():
    _, _, aliases = sample()
    sol = next(k for k, row in aliases.items() if row["model"]["id"] == "openai.gpt-6.2-sol")
    row = copy.deepcopy(aliases[sol])
    row["public_benchmarks"][0]["scores"].pop("Data Analysis")
    aliases[sol] = row
    assert primary_evidence(aliases)[1][sol]["mean"] is None


def test_preferences_share_the_same_pool_and_history_is_preserved():
    task, profile, aliases = sample()
    original = {"request": task.text, "task_profile": profile.model_dump(),
                "recent_conversation": [{"role": "user", "text": "预算47万元，不能变更"}],
                "current_tool_results": "verified balance 123"}
    payloads = []
    for tier in ("high", "medium", "low"):
        changed = task.model_copy(update={"routing_preferences": task.routing_preferences.model_copy(
            update={"cost_tier": tier})})
        payload = selection_payload(changed, original, aliases, None)
        state = json.loads(payload["state"])
        assert state["recent_conversation"] == original["recent_conversation"]
        assert state["current_tool_results"] == original["current_tool_results"]
        payloads.append(payload)
    assert len({json.loads(p["state"])["preference"] for p in payloads}) == 3
    assert all(p["questions"] == payloads[0]["questions"] for p in payloads)


def test_timeout_observation_is_configuration_bound_and_does_not_exclude_or_invent_p95():
    from app.bedrock_evidence import load_evidence
    task = Task(text="分析产线混杂因素", routing_preferences={"cost_tier": "high"})
    profile = replay_profile(task, "reasoning")
    evidence = load_evidence()
    observed = build_pool(task, profile, load_catalog(), load_rankings(), exclusion, evidence=evidence)
    original = copy.deepcopy(evidence)
    for entry in original["entries"].values():
        entry.pop("runtime_observations", None)
    before = build_pool(task, profile, load_catalog(), load_rankings(), exclusion, evidence=original)
    assert {r["candidate_id"] for r in before["candidates"]} == {
        r["candidate_id"] for r in observed["candidates"]}
    rows = [r for r in observed["candidates"] if r["model"]["id"] == "openai.gpt-6-astra"]
    default = next(r for r in rows if not r["benchmark"]["request_fields"])
    maximum = next(r for r in rows if r["benchmark"]["request_fields"] == {"reasoning": {"effort": "max"}})
    assert not default["runtime_observations"]
    assert maximum["runtime_observations"][0]["deadline_ms"] == 120000
    assert maximum["p95_ms"] is None
    aliases = {"m000": {**maximum, "workflow": "reasoning"},
               "m001": {**default, "workflow": "reasoning"}}
    payload = selection_payload(task, {"request": task.text}, aliases, None)
    cards = json.loads(payload["state"])["candidates"]
    assert "exceeded the 120-second response deadline with output limit 16384" in cards["m000"]
    assert "timeout observation" not in cards["m001"]
    assert "openai.gpt-6-astra" not in payload["state"]


def test_runtime_observations_never_transfer_to_another_endpoint_or_workflow():
    from app.bedrock_evidence import load_evidence, measured, runtime_observations
    catalog = load_catalog()
    model = next(m for m in catalog["models"] if m["id"] == "openai.gpt-6-astra")
    evidence = load_evidence()
    entry = measured(model, evidence)
    fields = {"reasoning": {"effort": "max"}}
    assert runtime_observations(entry, fields, {}, "reasoning")
    assert not runtime_observations(entry, fields, {}, "coding")
    assert not runtime_observations(entry, fields, {"temperature": 0.1}, "reasoning")
    assert not runtime_observations(measured({**model, "invoke_id": "another"}, evidence),
                                    fields, {}, "reasoning")
