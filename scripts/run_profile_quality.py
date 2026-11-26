"""Full isolated evaluation of frozen Liquid profile -> CPU policy -> Bedrock."""

import argparse
import asyncio
import copy
import gzip
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import boto3
import httpx
from botocore.config import Config
from fastapi import HTTPException

from app.agent_model import readable, validate_native_messages
from app.bedrock_embeddings import embed
from app.bedrock_evidence import load_evidence
from app.gateway_conversation import Conversation, SessionOptions
from app.gateway_decision import GatewayDecisionService
from app.gateway_effect_judge import grade
from app.gateway_media import MediaStore
from app.gateway_model_pool import build_pool
from app.gateway_store import Records
from app.industry_scenarios import invoke_tool, system_prompt
from app.routing import Task, exclusion, execute_selected, load_catalog, load_rankings
from app.routing_balance import preferences
from app.routing_media import resolve_workflow
from app.routing_profile import parse_profile, profile_payload
from scripts.measure_bedrock_specialized import cosine
from scripts.profile_policy import VERSION, choose
from scripts.profile_quality_cases import catalog

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2).encode()
    if path.suffix == ".gz":
        content = gzip.compress(content, mtime=0)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


def read(path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == ".gz" else data)


class Evaluation:
    policy_version = VERSION
    policy_source = ROOT / "scripts/profile_policy.py"
    selection_source = "profile_cpu_experiment"

    def suite(self):
        manifest = catalog()
        manifest.update(policy_version=self.policy_version,
            policy_sha256=hashlib.sha256(self.policy_source.read_bytes()).hexdigest(),
            catalog_version=self.catalog["version"], ranking_version=self.rankings["version"],
            measurement_version=self.evidence["version"])
        return manifest

    def __init__(self, args, http):
        self.args, self.http = args, http
        self.catalog, self.rankings, self.evidence = load_catalog(), load_rankings(), load_evidence()
        self.manifest = self.suite()
        manifest = args.output / "manifest.json"
        if manifest.exists() and read(manifest) != self.manifest:
            raise ValueError("Resume requires exactly the frozen suite, policy and evidence")
        save(manifest, self.manifest)
        snapshot = args.output / "evidence-snapshot.json.gz"
        if not snapshot.exists():
            save(snapshot, {"catalog": self.catalog, "rankings": self.rankings, "measurements": self.evidence})
        if os.getenv("GATEWAY_DATA_TABLE") or os.getenv("GATEWAY_MEDIA_BUCKET"):
            raise ValueError("Use local evaluation storage, not production data or media storage")
        records = Records(path=args.output / "private" / "media.sqlite3")
        media = MediaStore(records)
        media.local = args.output / "private" / "media"
        client = boto3.client("bedrock-runtime", region_name="us-east-1",
                              config=Config(connect_timeout=10, read_timeout=120,
                                            retries={"total_max_attempts": 1}, max_pool_connections=8))
        self.state = SimpleNamespace(bedrock=client, bedrock_target=client, bedrock_agent_target=client,
                                     bedrock_slots=asyncio.Semaphore(2), media=media)
        self.decision = GatewayDecisionService(http)
        self.decision.services["liquid"].update(base=args.gpu_url.rstrip("/"), key=boto3.client(
            "secretsmanager", region_name="us-east-1").get_secret_value(
                SecretId="DeciderModelRouter/gpu-service-key")["SecretString"])
        self.workers = asyncio.Semaphore(2)

    def runtime(self, task, history, *, native=None, item=None):
        runtime = object.__new__(Conversation)
        runtime.user = "evaluation"
        runtime.options = SessionOptions(fallback=False, prompt_cache=True, stream=False)
        runtime.health, runtime.previous = {}, None
        runtime.send, runtime.partial_output = None, False
        runtime.agent_tools = native is not None
        runtime.messages = copy.deepcopy(history) + [{
            "role": "user", "text": task.text, "attachments": [r.model_dump() for r in task.attachments]}]
        if native is not None:
            runtime.native_messages = copy.deepcopy(validate_native_messages(native))
            runtime.agent_system = system_prompt(item)
            runtime.messages = [{"role": m["role"], "text": readable(m), "attachments": []} for m in native]
        runtime.context = {"history_turns": (len(runtime.messages) - 1) // 2,
                           "text_bytes": sum(len(m["text"].encode()) for m in runtime.messages)}
        if native is not None:
            from app.industry_scenarios import TOOL_SPECS
            runtime.context["text_bytes"] += len(runtime.agent_system.encode()) + len(json.dumps(TOOL_SPECS).encode())
        return runtime

    async def prepare(self, case):
        data = copy.deepcopy(case["task"])
        opts = data.setdefault("routing_preferences", {})
        opts.update(max_output_tokens=min(opts.get("max_output_tokens", 16384), 16384),
                    expected_output_tokens=min(opts.get("expected_output_tokens", 512),
                                               opts.get("max_output_tokens", 16384)))
        refs = []
        for name in case.get("fixtures") or ([case["fixture"]] if case.get("fixture") else []):
            path = ROOT / "app/evaluation_assets" / name
            mime = {".png": "image/png", ".wav": "audio/wav", ".mp4": "video/mp4"}[path.suffix]
            asset = await asyncio.to_thread(self.state.media.put, "evaluation", path.read_bytes(), mime)
            refs.append({"id": asset["id"], "kind": asset["kind"]})
        if refs:
            data["attachments"] = refs
        return Task(**data)

    async def route(self, task, runtime, *, previous=None):
        started = time.perf_counter()
        task = runtime.task(task)
        request = SimpleNamespace(app=SimpleNamespace(state=self.state),
                                  state=SimpleNamespace(user={"username": "evaluation"}, gateway_runtime=runtime))
        payload = runtime.classify(profile_payload(task))
        response = await self.decision.infer(SimpleNamespace(provider="liquid"), request, payload)
        assessed = parse_profile(response)
        workflow, reason = resolve_workflow(task, assessed)
        profile = assessed.model_copy(update={"workflow": workflow})
        pool_start = time.perf_counter()
        pool = build_pool(task, profile, self.catalog, self.rankings, exclusion, runtime, evidence=self.evidence)
        pool_ms = (time.perf_counter() - pool_start) * 1000
        policy_start = time.perf_counter()
        selection = choose(profile.model_dump(), pool["candidates"], cost_tier=preferences(task).cost_tier,
                           previous_candidate_id=previous)
        policy_ms = (time.perf_counter() - policy_start) * 1000
        chosen = next((r for r in pool["candidates"]
                       if r["candidate_id"] == selection["selected_candidate_id"]), None)
        record = {
            "assessed_profile": assessed.model_dump(), "profile": profile.model_dump(),
            "workflow_override": reason, "gpu": response, "gpu_payload": payload,
            "pool_ms": pool_ms, "cpu_policy_ms": policy_ms,
            "route_ms": (time.perf_counter() - started) * 1000, "selection": selection,
            "excluded": pool["excluded"], "selected_candidate": chosen,
            "execution_ready": pool["execution_ready"], "execution_notice": pool["execution_notice"],
            "context": runtime.context, "task": task.model_dump(mode="json"),
        }
        return record, chosen, request, task, workflow

    def export_artifacts(self, output, cid):
        for index, asset in enumerate(output.get("artifacts", [])):
            metadata, raw = self.state.media.read("evaluation", asset["id"])
            path = self.args.output / "artifacts" / f"{cid}-{index}.{metadata['format']}"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(raw)
            asset["local_path"] = str(path.relative_to(self.args.output))

    async def retrieval(self, row, data):
        client = self.state.bedrock_target
        started = time.perf_counter()
        vectors = [await asyncio.to_thread(embed, client, row["model"], text, document=True)
                   for text in data["documents"]]
        tests = []
        for query in data["queries"]:
            vector, _ = await asyncio.to_thread(embed, client, row["model"], query["text"])
            scores = [cosine(vector, x[0]) for x in vectors]
            order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            rank = order.index(query["relevant"]) + 1
            tests.append({**query, "order": order, "rank": rank})
        import math
        return {
            "complete": True, "text": "Cross-language retrieval benchmark", "retrieval": tests,
            "recall_at_1": sum(t["rank"] == 1 for t in tests) / len(tests),
            "recall_at_3": sum(t["rank"] <= 3 for t in tests) / len(tests),
            "ndcg_at_3": sum(1 / math.log2(t["rank"] + 1) if t["rank"] <= 3 else 0 for t in tests) / len(tests),
            "actual_model_calls": len(vectors) + len(tests),
            "backend_total_ms": (time.perf_counter() - started) * 1000,
            "usage": None, "observed_reference_cost_usd": None,
        }

    async def one(self, case, folder="base"):
        path = self.args.output / folder / (case["id"] + ".json.gz")
        if path.exists():
            return
        row = {"case_id": case["id"], "case": case, "started_at": datetime.now(UTC).isoformat(),
               "status": "started", "provider": "liquid", "arm": "routed"}
        if case.get("gap"):
            row.update(status="capability_gap", grade={"status": "unmeasured", "reason": case["gap"]})
            save(path, row)
            return
        async with self.workers:
            save(path, row)  # An interrupted attempt is never silently retried.
            try:
                task = await self.prepare(case)
                runtime = self.runtime(task, case.get("history", []))
                route, candidate, request, task, workflow = await self.route(task, runtime)
                row.update(route=route, route_ms=route["route_ms"],
                           workflow_fit=workflow in case["allowed_workflows"])
                if not candidate:
                    row.update(status="no_route", grade={"status": "fail", "reason": "No eligible model"})
                elif not route["execution_ready"]:
                    row.update(status="execution_blocked", grade={"status": "fail", "reason": route["execution_notice"]})
                else:
                    row["target_started_at"] = datetime.now(UTC).isoformat()
                    save(path, row)
                    if case.get("retrieval"):
                        output = await self.retrieval(candidate, case["retrieval"])
                    else:
                        output = await execute_selected(request, candidate, task, workflow,
                                                        self.selection_source, self.policy_version, self.rankings["version"])
                    self.export_artifacts(output, case["id"])
                    row.update(output=output, status="completed" if output["complete"] else "incomplete",
                               observation_id=hashlib.sha256(case["id"].encode()).hexdigest()[:24])
                    if case.get("retrieval"):
                        row["grade"] = {"status": "pass" if output["recall_at_1"] == 1 else "fail",
                                        "source": "labeled_retrieval", "reason": "all four queries must rank the relevant document first"}
                    else:
                        row["grade"] = grade(case, output)
                if case.get("expected_boundary_statuses"):
                    row["boundary_check"] = row["status"] in case["expected_boundary_statuses"]
            except Exception as exc:
                row.update(status="error", error={"type": type(exc).__name__,
                    "message": str(exc.detail) if isinstance(exc, HTTPException) else str(exc)},
                    grade={"status": "fail", "source": "execution"})
            save(path, row)
            print(json.dumps({"case": case["id"], "status": row["status"],
                              "grade": row.get("grade", {}).get("status")}), flush=True)

    async def industry(self, item):
        path = self.args.output / "industries" / (item["id"] + ".json.gz")
        existing = None
        if self.args.resume_blocked and not path.exists():
            return
        if path.exists():
            existing = read(path)
            if (not self.args.resume_blocked or existing["status"] != "completed"
                    or not any(t["status"] == "dependency_blocked" for t in existing["turns"])):
                return
            archived = self.args.output / "dependency-initial" / path.name
            if not archived.exists():
                save(archived, existing)
        report = existing or {"scenario": item, "status": "running", "turns": [],
                              "execution": "local_native_agent_harness_not_agentcore_runtime"}
        native, previous = [], None
        done = {t["number"]: t for t in report["turns"] if t["status"] != "dependency_blocked"}
        report["turns"] = list(done.values())
        if existing:
            report["continuation_method"] = "new later turns use only earlier successful turns; failed target calls are not retried"
        for completed in report["turns"]:
            if completed["status"] != "completed":
                continue
            native.append({"role": "user", "content": [{"text": completed["task"]}]})
            for part in completed["steps"]:
                native.append(part["output"]["message"])
                if part.get("tool_results"):
                    native.append({"role": "user", "content": part["tool_results"]})
                previous = part["route"]["selection"]["selected_candidate_id"]
        async with self.workers:
            report["status"] = "running"
            save(path, report)
            for index, text in enumerate(item["turns"]):
                if index + 1 in done:
                    continue
                prior_native, prior_configuration = copy.deepcopy(native), previous
                turn = {"number": index + 1, "task": text, "steps": [], "status": "started",
                        "grade": {"status": "pending", "reason": "Business and code review required"}}
                report["turns"].append(turn)
                native.append({"role": "user", "content": [{"text": text}]})
                try:
                    for step in range(6):
                        task = Task(text=text, routing_preferences={"max_output_tokens": 16384,
                                                                   "expected_output_tokens": 512})
                        runtime = self.runtime(task, [], native=native, item=item)
                        route, candidate, request, task, workflow = await self.route(task, runtime, previous=previous)
                        part = {"step": step + 1, "route": route, "status": "started"}
                        turn["steps"].append(part)
                        save(path, report)
                        if not candidate:
                            raise ValueError("No eligible agent model")
                        output = await execute_selected(request, candidate, task, workflow,
                                                        self.selection_source, self.policy_version, self.rankings["version"])
                        part.update(output=output, status="completed" if output["complete"] else "incomplete")
                        if not output["complete"]:
                            turn.update(status="incomplete", answer=output.get("text", ""))
                            break
                        previous = candidate["candidate_id"]
                        native.append(output["message"])
                        if output["turn_complete"]:
                            turn.update(status="completed", answer=output["text"])
                            break
                        tools = []
                        for call in output["tool_calls"]:
                            try:
                                value = invoke_tool(item, call["name"], call["input"])
                                status = "success"
                            except ValueError as exc:
                                value, status = {"error": str(exc)}, "error"
                            tools.append({"toolResult": {"toolUseId": call["toolUseId"],
                                "content": [{"json": value}], "status": status}})
                        part["tool_results"] = tools
                        native.append({"role": "user", "content": tools})
                    else:
                        turn.update(status="call_limit", grade={"status": "fail", "reason": "Six model calls exhausted"})
                except Exception as exc:
                    turn.update(status="error", error=str(exc.detail) if isinstance(exc, HTTPException) else str(exc),
                                grade={"status": "fail", "source": "execution"})
                save(path, report)
                print(json.dumps({"industry": item["id"], "turn": index + 1,
                                  "status": turn["status"], "model_calls": len(turn["steps"])}), flush=True)
                if turn["status"] != "completed":
                    # Match gateway history semantics: a failed turn does not become
                    # successful history. A later user turn can still be attempted.
                    native, previous = prior_native, prior_configuration
            report.update(status="completed", native_messages=native)
            save(path, report)

    async def run(self):
        if self.args.group in {"base", "all"}:
            await asyncio.gather(*(self.one(c) for c in self.manifest["base_cases"]))
            await asyncio.gather(*(self.one(c, "additional") for c in self.manifest["additional_cases"]))
        if self.args.group in {"industries", "all"}:
            await asyncio.gather(*(self.industry(s) for s in self.manifest["industry_scenarios"]))
        self.state.bedrock.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu-url", default="http://127.0.0.1:23124")
    parser.add_argument("--group", choices=["base", "industries", "all"], default="all")
    parser.add_argument("--resume-blocked", action="store_true",
                        help="Attempt later untested turns using successful history; never retry failed calls")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(trust_env=False) as http:
        await Evaluation(args, http).run()


if __name__ == "__main__":
    asyncio.run(main())
