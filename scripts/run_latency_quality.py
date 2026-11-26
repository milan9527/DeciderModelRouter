"""Fresh production two-stage routes and Bedrock outputs on a frozen paired suite."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx

from app.agent_model import AgentConversation
from app.gateway_model_pool import MODEL_ROUTING_VERSION, build_pool
from app.public_benchmarks import load_public_benchmarks
from app.routing import exclusion
from app.routing_media import resolve_workflow
from app.routing_profile import parse_profile, profile_payload
from scripts.run_profile_quality import ROOT, Evaluation, read, save


class LatencyEvaluation(Evaluation):
    selection_source = "production_two_stage_latency_evaluation"

    def suite(self):
        frozen = read(self.args.suite)
        if getattr(self.args, "repeat_regression", False):
            frozen["phase"] = "repeated_after_configuration_quality_fix"
            frozen["evaluation_limits"] = [
                "All 56 tasks have already been executed; this final run is visible development regression.",
                "The new_holdout split denotes the 12 tasks' initial origin only; final results are not blind holdout accuracy.",
                *frozen.get("evaluation_limits", [])[2:],
            ]
        return {
            **frozen, "arm": self.args.arm,
            "public_benchmark_version": load_public_benchmarks()["version"],
            "catalog_version": self.catalog["version"],
            "ranking_version": self.rankings["version"],
            "measurement_version": self.evidence["version"],
            "sources": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [ROOT / "scripts/run_latency_quality.py",
                                  *sorted((ROOT / "app").glob("*.py"))]},
        }

    def __init__(self, args, http):
        self.policy_version = MODEL_ROUTING_VERSION
        super().__init__(args, http)
        self.decision.services["liquid"]["path"] = args.gpu_path
        self.router_lock = asyncio.Lock()

    def runtime(self, task, history, *, native=None, item=None):
        runtime = super().runtime(task, history, native=native, item=item)
        if native is not None:
            # Use the production native tool/history assessment and token accounting.
            runtime.__class__ = AgentConversation
            runtime.context.update(
                source="agent_native",
                text_bytes=len(json.dumps(native, ensure_ascii=False).encode())
                + len(runtime.agent_system.encode()),
            )
            from app.industry_scenarios import TOOL_SPECS
            runtime.context["text_bytes"] += len(json.dumps(TOOL_SPECS).encode())
        return runtime

    async def route(self, task, runtime, *, previous=None):
        queued = time.perf_counter()
        async with self.router_lock:
            queue_ms = (time.perf_counter() - queued) * 1000
            started = time.perf_counter()
            calls = []
            original = self.decision.infer

            async def capture(body, request, payload, **kwargs):
                call = {"number": len(calls) + 1, "payload": payload, "status": "started"}
                calls.append(call)
                try:
                    result = await original(body, request, payload, **kwargs)
                    call.update(status="completed", result=result)
                    return result
                except Exception as exc:
                    call.update(status="error", error=str(getattr(exc, "detail", exc)))
                    raise

            self.decision.infer = capture
            try:
                runtime.previous = {"configuration_id": previous} if previous else None
                actual = runtime.task(task)
                request = SimpleNamespace(
                    app=SimpleNamespace(state=self.state),
                    state=SimpleNamespace(user={"username": "evaluation"}, gateway_runtime=runtime))
                body = SimpleNamespace(task=actual, provider="liquid")
                payload = runtime.classify(profile_payload(actual))
                response = await self.decision.infer(body, request, payload)
                assessed = parse_profile(response)
                workflow, reason = resolve_workflow(actual, assessed)
                profile = assessed.model_copy(update={"workflow": workflow})
                pool = build_pool(actual, profile, self.catalog, self.rankings, exclusion,
                                  runtime, evidence=self.evidence)
                pool.update(profile_input=payload, assessment={
                    "round": 1, "mode": "assessment", "model": response["model"],
                    "timings": response["timings"], "usage": response.get("usage"),
                })
                pool = await self.decision.select(body, request, pool)
                chosen = next((r for r in pool["candidates"]
                               if r["candidate_id"] == pool.get("selected_candidate_id")), None)
                selection = {k: v for k, v in pool.items() if k not in {"candidates", "excluded"}}
                selection["selected_model_id"] = chosen["model"]["id"] if chosen else None
                return {
                    "arm": self.args.arm, "assessed_profile": assessed.model_dump(),
                    "profile": profile.model_dump(), "workflow_override": reason,
                    "gpu_calls": calls, "selected_candidate": chosen, "selection": selection,
                    "excluded": pool["excluded"], "execution_ready": pool["execution_ready"],
                    "execution_notice": pool["execution_notice"], "context": runtime.context,
                    "task": actual.model_dump(mode="json"), "experiment_queue_ms": queue_ms,
                    "route_ms": (time.perf_counter() - started) * 1000,
                    "timing_boundary": "local CPU + private HTTP; excludes evaluation admission queue",
                }, chosen, request, actual, workflow
            except Exception:
                save(self.args.output / "route-errors" / f"{time.time_ns()}.json.gz", {
                    "task": task.model_dump(mode="json"), "gpu_calls": calls})
                raise
            finally:
                self.decision.infer = original

    async def run(self):
        try:
            if self.args.group in {"base", "all"}:
                await asyncio.gather(*(self.one(c) for c in self.manifest["cases"]))
            if self.args.group in {"industries", "all"}:
                await asyncio.gather(*(self.industry(s) for s in self.manifest["industries"]))
        finally:
            self.state.bedrock.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--arm", choices=["baseline", "optimized"], required=True)
    parser.add_argument("--gpu-url", default="http://127.0.0.1:23124")
    parser.add_argument("--gpu-path", default="/v1/systemone/liquid")
    parser.add_argument("--group", choices=["base", "industries", "all"], default="all")
    parser.add_argument("--repeat-regression", action="store_true")
    args = parser.parse_args()
    args.resume_blocked = False
    async with httpx.AsyncClient(trust_env=False) as client:
        await LatencyEvaluation(args, client).run()


if __name__ == "__main__":
    asyncio.run(main())
