"""Fresh, isolated three-arm calls with identical tasks and rotating submission order."""

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx

from app.gateway_joint_decision import joint_payload
from app.gateway_model_decision import finish_selection
from app.gateway_model_pool import build_pool
from app.routing import exclusion
from app.routing_media import resolve_workflow
from app.routing_profile import parse_profile, profile_payload
from scripts.paired_quality_cases import cases, version
from scripts.run_profile_quality import ROOT, Evaluation, read, save

ARMS = ("joint", "two_stage", "cpu_policy")
VERSIONS = {
    "joint": "gpu-evidence-single-pass-v2",
    "two_stage": "gpu-profile-select-paired-v1",
    "cpu_policy": "profile-cpu-policy-experiment-v1",
}


def ordered_arms(index):
    offset = index % len(ARMS)
    return ARMS[offset:] + ARMS[:offset]


def stage_two_payload(body, request, original, profile, candidates):
    seed = hashlib.sha256(body.task.text.encode()).hexdigest()
    rows = sorted(candidates, key=lambda r: hashlib.sha256((seed + r["candidate_id"]).encode()).hexdigest())
    rows = [{**r, "workflow": profile.workflow} for r in rows]
    state = json.loads(original["state"])
    state["task_profile"] = profile.model_dump()
    payload, aliases = joint_payload(
        body, request, {"state": json.dumps(state, ensure_ascii=False), "questions": {}}, rows)
    source = (
        "Infer its needs directly; the other questions are answered simultaneously, not prior results. "
        "All listed configurations remain eligible for at least one workflow. "
        "Use the matching workflow's tests, cost and latency. "
    )
    assert source in payload["questions"]["action"]["instructions"]
    payload["questions"]["action"]["instructions"] = payload["questions"]["action"]["instructions"].replace(
        source, "The task_profile in state is the completed first-stage GPU assessment. "
        "Select using that assessment and the matching workflow tests, cost and latency. ")
    return payload, aliases


class PairedEvaluation(Evaluation):
    def __init__(self, args, http, router_lock, workers):
        self.arm = args.arm
        self.policy_version = VERSIONS[self.arm]
        self.selection_source = self.arm + "_paired_experiment"
        self.policy_source = ROOT / (
            "scripts/profile_policy.py" if self.arm == "cpu_policy" else
            "app/gateway_joint_decision.py" if self.arm == "joint" else
            "scripts/run_paired_quality.py")
        self.router_lock = router_lock
        super().__init__(args, http)
        self.workers = workers

    def suite(self):
        result = super().suite()
        result.update(experiment_arm=self.arm, holdout_cases=cases(), holdout_version=version(),
                      scope="fresh paired calls; 143 existing tasks plus 16 frozen new diagnostics")
        return result

    async def route(self, task, runtime, *, previous=None):
        queued = time.perf_counter()
        async with self.router_lock:
            queue_ms = (time.perf_counter() - queued) * 1000
            started = time.perf_counter()
            records = []
            original = self.decision.infer

            async def capture(body, request, payload, **kwargs):
                part = {"number": len(records) + 1, "payload": payload,
                        "status": "started", "flags": kwargs}
                records.append(part)
                call_started = time.perf_counter()
                try:
                    result = await original(body, request, payload, **kwargs)
                    part.update(result=result, status="completed")
                    return result
                except Exception as exc:
                    part.update(status="error", error=str(getattr(exc, "detail", exc)))
                    raise
                finally:
                    part["elapsed_ms"] = (time.perf_counter() - call_started) * 1000

            self.decision.infer = capture
            try:
                runtime.previous = {"configuration_id": previous} if previous else None
                if self.arm == "cpu_policy":
                    record, chosen, request, actual_task, workflow = await super().route(
                        task, runtime, previous=previous)
                else:
                    actual_task = runtime.task(task)
                    request = SimpleNamespace(app=SimpleNamespace(state=self.state),
                        state=SimpleNamespace(user={"username": "evaluation"}, gateway_runtime=runtime))
                    body = SimpleNamespace(task=actual_task, provider="liquid")
                    payload = runtime.classify(profile_payload(actual_task))
                    pool_ms = 0
                    if self.arm == "joint":
                        response, pool = await self.decision.assess(
                            body, request, payload, self.catalog, self.rankings, exclusion)
                        assessed = parse_profile(response)
                        profile = assessed.model_copy(update={"workflow": resolve_workflow(actual_task, assessed)[0]})
                    else:
                        response = await self.decision.infer(body, request, payload)
                        assessed = parse_profile(response)
                        profile = assessed.model_copy(update={"workflow": resolve_workflow(actual_task, assessed)[0]})
                        pool_started = time.perf_counter()
                        pool = build_pool(actual_task, profile, self.catalog, self.rankings, exclusion,
                                          runtime, evidence=self.evidence)
                        pool_ms = (time.perf_counter() - pool_started) * 1000
                        if pool["candidates"]:
                            selection_payload, aliases = stage_two_payload(
                                body, request, payload, profile, pool["candidates"])
                            selection_result = await self.decision.infer(body, request, selection_payload, selection=True)
                            key = selection_result["answers"]["action"]["choice"]
                            chosen_row = aliases.get(key)
                            cid = chosen_row["candidate_id"] if chosen_row else None
                            pool = finish_selection(body, request, pool, cid, [{
                                "model": selection_result["model"], "selected_candidate_id": cid,
                                "timings": selection_result["timings"],
                            }], (time.perf_counter() - started) * 1000, len(pool["candidates"]), mode="two_stage")
                    chosen = next((r for r in pool["candidates"]
                                   if r["candidate_id"] == pool.get("selected_candidate_id")), None)
                    workflow, reason = resolve_workflow(actual_task, assessed)
                    record = {
                        "assessed_profile": assessed.model_dump(), "profile": profile.model_dump(),
                        "workflow_override": reason, "gpu": response, "gpu_payload": payload,
                        "pool_ms": pool_ms if self.arm != "joint" else None,
                        "cpu_policy_ms": 0, "selection": {
                            k: v for k, v in pool.items() if k not in {"candidates", "excluded"}},
                        "excluded": pool["excluded"], "selected_candidate": chosen,
                        "execution_ready": pool["execution_ready"], "execution_notice": pool["execution_notice"],
                        "context": runtime.context, "task": actual_task.model_dump(mode="json"),
                    }
                    record["selection"]["selected_model_id"] = chosen["model"]["id"] if chosen else None
                record.update(arm=self.arm, gpu_calls=records, experiment_queue_ms=queue_ms,
                              route_ms=(time.perf_counter() - started) * 1000,
                              timing_boundary="same workspace CPU and SSM HTTP, excludes experiment admission queue")
                return record, chosen, request, actual_task, workflow
            except Exception:
                # Parent runner records the failed case. Keep failed GPU attempts too.
                save(self.args.output / "route-errors" / f"{time.time_ns()}.json.gz", {
                    "arm": self.arm, "task": task.model_dump(mode="json"), "gpu_calls": records,
                    "experiment_queue_ms": queue_ms, "route_ms": (time.perf_counter() - started) * 1000})
                raise
            finally:
                self.decision.infer = original


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu-url", default="http://127.0.0.1:23124")
    parser.add_argument("--group", choices=["all", "base", "industries", "holdout"], default="all")
    args = parser.parse_args()
    lock, workers = asyncio.Lock(), asyncio.Semaphore(2)
    async with httpx.AsyncClient(trust_env=False) as http:
        evaluations = {
            arm: PairedEvaluation(SimpleNamespace(**{
                **vars(args), "output": args.output / arm, "arm": arm, "resume_blocked": False,
            }), http, lock, workers) for arm in ARMS
        }
        source_paths = [
            "scripts/run_profile_quality.py", "scripts/run_paired_quality.py",
            "scripts/paired_quality_cases.py", "scripts/profile_policy.py",
            "scripts/profile_quality_cases.py", "app/gateway_joint_decision.py",
            "app/gateway_model_pool.py", "app/gateway_model_decision.py",
            "app/gateway_conversation.py", "app/agent_model.py", "app/industry_scenarios.py",
        ]
        manifest = {
            "arms": VERSIONS, "planned_per_arm": 159, "existing_per_arm": 143, "new_per_arm": 16,
            "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in source_paths},
            "base_suite_version": evaluations["joint"].manifest["version"],
            "holdout_version": version(), "max_output_tokens": 16384, "expected_output_tokens": 512,
            "target_retry": False, "fallback": False, "stream": False,
            "prompt_cache": True, "industry_execution": "local_native_adapter_not_agentcore",
            "order": "rotating arm submission order, maximum two active case/scenario workers",
            "gpu_concurrency": 1, "history": "independent successful native history per arm, no cross-arm answer reuse",
            "cache_caveat": "Provider caches may overlap across arms selecting identical models/prefixes",
            "reference_answers_visible_to_targets": False,
            "new_cases": cases(),
        }
        path = args.output / "manifest.json"
        if path.exists() and read(path) != manifest:
            raise ValueError("Experiment manifest changed; create a new run instead of mixing results")
        save(path, manifest)
        if not (args.output / "started.json").exists():
            save(args.output / "started.json", {"at": datetime.now(UTC).isoformat()})
        suite = evaluations["joint"].manifest
        try:
            if args.group in {"all", "base"}:
                jobs = [(g, c) for g, rows in [("base", suite["base_cases"]), ("additional", suite["additional_cases"])]
                        for c in rows]
                for index, (group, case) in enumerate(jobs):
                    await asyncio.gather(*(evaluations[a].one(case, group) for a in ordered_arms(index)))
            if args.group in {"all", "holdout"}:
                for index, case in enumerate(cases()):
                    await asyncio.gather(*(evaluations[a].one(case, "holdout") for a in ordered_arms(index)))
            if args.group in {"all", "industries"}:
                for index, item in enumerate(suite["industry_scenarios"]):
                    await asyncio.gather(*(evaluations[a].industry(item) for a in ordered_arms(index)))
        finally:
            for evaluation in evaluations.values():
                evaluation.state.bedrock.close()
        save(args.output / f"finished-{args.group}.json", {"at": datetime.now(UTC).isoformat()})


if __name__ == "__main__":
    asyncio.run(main())
