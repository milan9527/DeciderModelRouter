import copy
import gzip
import hashlib
import importlib.util
import json
import random
import subprocess
from pathlib import Path
from types import SimpleNamespace

from app.gateway_joint_decision import joint_payload
from app.routing import Task

root = Path(__file__).parent
oldpath = root / "baseline_joint.py"
oldpath.write_bytes(subprocess.check_output(["git", "show", "HEAD:app/gateway_joint_decision.py"]))
spec = importlib.util.spec_from_file_location("baseline_joint", oldpath)
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)
source = Path("validation/independent_gateway/livebench-preference-2026-11-26/live")
items = [
    "manufacturing-liquid", "insurance", "coding", "general", "history-followup",
    "agent-first-step-1", "agent-followup-step-1",
]
cases = []
metadata = {}
for item in items:
    run = json.loads(gzip.decompress((source / (item + ".json.gz")).read_bytes()))
    selection = next(e["data"]["selection"] for e in run["events"]
                     if e.get("stage") == "selection" and e.get("status") == "completed")
    original_state = json.loads(next(e["data"]["model_input"]["state"] for e in run["events"]
                                    if e.get("stage") == "classification" and e.get("status") == "completed"))
    seed = hashlib.sha256(run["request"]["task"]["text"].encode()).hexdigest()
    rows = sorted(selection["candidates"],
                  key=lambda r: hashlib.sha256((seed + r["candidate_id"]).encode()).hexdigest())
    rows = [{**r, "workflow": selection["profile"]["workflow"]} for r in rows]
    for tier in ("high", "medium", "low"):
        task = Task(**run["request"]["task"])
        task = task.model_copy(update={"routing_preferences": task.routing_preferences.model_copy(
            update={"cost_tier": tier})})
        state = {**original_state, "task_profile": selection["profile"],
                 "quality_policy": {k: selection["quality_policy"][k] for k in (
                     "version", "active", "comparison", "note", "cost_tier", "preference_handling")}}
        state["quality_policy"]["cost_tier"] = tier
        body = SimpleNamespace(task=task)
        request = SimpleNamespace(state=SimpleNamespace())
        for version, fn in (("baseline", baseline.joint_payload), ("new", joint_payload)):
            payload, aliases = fn(body, request, {"state": json.dumps(state), "questions": {}},
                                  rows, assessed=True)
            for order in range(2):
                p = copy.deepcopy(payload)
                if order:
                    q = p["questions"]["action"]
                    pairs = list(q["criteria"].items())
                    random.Random(202611 + order).shuffle(pairs)
                    q["criteria"] = dict(pairs)
                    st = json.loads(p["state"])
                    if "candidates" in st:
                        pairs = list(st["candidates"].items())
                        random.Random(9112026 + order).shuffle(pairs)
                        st["candidates"] = dict(pairs)
                        p["state"] = json.dumps(st, ensure_ascii=False, separators=(",", ":"))
                name = f"{item}/{tier}/{version}/{order}"
                cases.append({"name": name, "payload": p})
                metadata[name] = {
                    "case": item, "tier": tier, "version": version, "order": order,
                    "profile": selection["profile"],
                    "aliases": {k: {"id": r["model"]["id"], "configuration": r["benchmark"]["request_fields"],
                                    "candidate_id": r["candidate_id"],
                                    "evidence": [{key: e.get(key) for key in (
                                        "source", "benchmark", "release", "scores", "tested_request_fields")}
                                        for e in r["public_benchmarks"]]}
                                for k, r in aliases.items()},
                }
for batch in range(0, len(cases), 6):
    path = root / f"cases-paired-{batch//6}.json"
    path.write_text(json.dumps(cases[batch:batch+6], ensure_ascii=False))
    packed_size = len(gzip.compress(path.read_bytes())) * 4 // 3
    assert packed_size < 45000, packed_size
(root / "paired-metadata.json").write_text(json.dumps(metadata, ensure_ascii=False))
print("Prepared frozen paired diagnosis", len(cases), "requests;", len(metadata), "aliases")
