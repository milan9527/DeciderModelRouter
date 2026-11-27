"""Evaluate a frozen suite through the authenticated public gateway and AgentCore."""

import argparse
import asyncio
import copy
import gzip
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import boto3
import httpx

from app.gateway_effect_judge import grade


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode()
    if path.suffix == ".gz":
        raw = gzip.compress(raw, mtime=0)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)


def read(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def now():
    return datetime.now(UTC).isoformat()


class Evaluation:
    def __init__(self, args, client):
        self.args, self.client = args, client
        self.suite = read(args.suite)
        self.slots = asyncio.Semaphore(2)
        self.catalog = None

    async def get(self, url):
        response = await self.client.get(url)
        response.raise_for_status()
        return response.json()

    async def one(self, case):
        path = self.args.output / "base" / (case["id"] + ".json.gz")
        if path.exists():
            return read(path)
        row = {"case_id": case["id"], "case": case, "started_at": now(), "status": "started",
               "provider": "liquid", "arm": "online", "events": []}
        async with self.slots:
            save(path, row)
            try:
                task = copy.deepcopy(case["task"])
                opts = task.setdefault("routing_preferences", {})
                opts.setdefault("max_output_tokens", 16384)
                opts.setdefault("expected_output_tokens", 512)
                refs = []
                for name in case.get("fixtures") or ([case["fixture"]] if case.get("fixture") else []):
                    asset = Path("app/evaluation_assets") / name
                    mime = {".png": "image/png", ".wav": "audio/wav", ".mp4": "video/mp4"}[asset.suffix]
                    response = await self.client.post("/api/gateway/media", content=asset.read_bytes(),
                                                      headers={"Content-Type": mime})
                    response.raise_for_status()
                    uploaded = response.json()
                    refs.append({"id": uploaded["id"], "kind": uploaded["kind"]})
                if refs:
                    task["attachments"] = refs
                sid = None
                if case.get("depends_on"):
                    previous = read(self.args.output / "base" / (case["depends_on"] + ".json.gz"))
                    sid = previous.get("session_id")
                    if not sid:
                        raise ValueError("Previous conversation turn has no session ID; do not invent history")
                body = {
                    **{k: self.catalog[k] for k in ("catalog_version", "ranking_version", "policy_version")},
                    "task": task, "provider": "liquid", "execute_target": True,
                    "client_request_id": str(uuid4()), "session_id": sid,
                    "session": {"use_history": bool(case.get("conversation_group")), "stickiness": False,
                                "prompt_cache": True, "stream": True, "fallback": False},
                }
                if "history" in case:
                    body["history"] = case["history"]
                row["request"] = body
                save(path, row)
                started = time.monotonic()
                try:
                    async with self.client.stream("POST", "/api/gateway/runs", json=body) as response:
                        if response.status_code != 200:
                            await response.aread()
                            response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            event = json.loads(line)
                            row["events"].append(event)
                            row["run_id"] = event.get("request_id") or row.get("run_id")
                            if event.get("type") != "token":
                                save(path, row)
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    row["transport_error"] = {"type": type(exc).__name__, "message": str(exc)}
                row["wall_ms"] = (time.monotonic() - started) * 1000
                if not row.get("run_id"):
                    raise ValueError("Submission returned no run ID; not retried")
                history = await self.get("/api/gateway/history/" + row["run_id"])
                # Recover an interrupted response using GET only; never resubmit generation.
                deadline = time.monotonic() + 180
                while history.get("status") in {"running", "started", "classifying", "selecting", "executing"}:
                    if time.monotonic() > deadline:
                        break
                    await asyncio.sleep(2)
                    history = await self.get("/api/gateway/history/" + row["run_id"])
                save(self.args.output / "history" / (row["run_id"] + ".json.gz"), history)
                row["session_id"] = history.get("session_id")
                row["online_status"] = history.get("status")
                events = history.get("events", row["events"])
                selected = next((e["data"] for e in events
                                 if e.get("stage") == "selection" and e.get("status") == "completed"), {})
                output = next((e["data"] for e in reversed(events)
                               if e.get("stage") == "execution" and e.get("status") == "completed"), {})
                selection = selected.get("selection", {})
                model = selected.get("selected_model") or {}
                row["route"] = {
                    "selection": {**selection, "selected_model_id": model.get("id")},
                    "profile": history.get("profile"), "route_ms": history.get("route_ms"),
                }
                row["route_ms"] = history.get("route_ms")
                row["versions"] = {k: history.get(k) for k in (
                    "catalog_version", "ranking_version", "policy_version", "measurement_version",
                    "model_routing_version", "public_benchmark_version")}
                row["output"] = output
                row["status"] = ("completed" if output.get("complete") and history.get("status") == "executed"
                                 else "incomplete" if output else "error")
                row["errors"] = [e.get("data") for e in events if e.get("type") == "error"]
                row["grade"] = ({"status": "unmeasured", "content_status": "unmeasured",
                                 "reason": "Public endpoint returns vector preview only; full retrieval ranking not measurable"}
                                if case.get("retrieval") else grade(case, output))
                if row["status"] != "completed":
                    row["grade"] = {"status": "fail", "content_status": "fail", "source": "execution",
                                    "reason": "No complete production answer"}
                row["events"] = []  # Full events are preserved in the history artifact.
            except Exception as exc:
                row.update(status="error", error={"type": type(exc).__name__, "message": str(exc)},
                           grade={"status": "fail", "content_status": "fail", "source": "execution"})
            row["finished_at"] = now()
            save(path, row)
            print(json.dumps({"case": case["id"], "status": row["status"],
                              "grade": row.get("grade", {}).get("status"),
                              "model": row.get("route", {}).get("selection", {}).get("selected_model_id")},
                             ensure_ascii=False), flush=True)
        return row

    async def conversation(self, cases):
        for case in cases:
            await self.one(case)

    async def industry(self, scenario):
        path = self.args.output / "industries" / (scenario["id"] + ".json.gz")
        report = read(path) if path.exists() else {"scenario_id": scenario["id"], "turns": []}
        sid = report.get("session_id")
        for number, prompt in enumerate(scenario["turns"], 1):
            if any(t["number"] == number for t in report["turns"]):
                continue
            turn = {"number": number, "prompt": prompt, "started_at": now(), "status": "started"}
            report["turns"].append(turn)
            save(path, report)
            try:
                body = {"scenario_id": scenario["id"], "prompt": prompt, "session_id": sid,
                        "client_request_id": str(uuid4()), "routing_preferences": {
                            "cost_tier": "medium", "expected_output_tokens": 512, "max_output_tokens": 16384}}
                turn["request"] = body
                save(path, report)
                response = await self.client.post("/api/gateway/agent/jobs", json=body)
                response.raise_for_status()
                job = response.json()
                turn["job_id"] = job["id"]
                sid = job["session_id"]
                report["session_id"] = sid
                save(path, report)
                deadline = time.monotonic() + 720
                while job["status"] == "running" and time.monotonic() < deadline:
                    save(self.args.output / "agent-jobs" / (job["id"] + ".json.gz"), job)
                    await asyncio.sleep(2)
                    job = await self.get("/api/gateway/agent/jobs/" + job["id"])
                save(self.args.output / "agent-jobs" / (job["id"] + ".json.gz"), job)
                turn.update(status=job["status"], answer=job.get("answer", ""), steps=job.get("steps", []),
                            metrics=job.get("metrics"), platform_version=job.get("platform_version"),
                            error=job.get("error"), grade={"status": "pending", "source": "manual_required"})
                if job["status"] != "completed":
                    turn["grade"] = {"status": "fail", "content_status": "fail", "source": "execution"}
                for step in job.get("steps", []):
                    if step.get("run_id"):
                        history = await self.get("/api/gateway/history/" + step["run_id"])
                        save(self.args.output / "history" / (step["run_id"] + ".json.gz"), history)
            except Exception as exc:
                turn.update(status="error", error={"type": type(exc).__name__, "message": str(exc)},
                            grade={"status": "fail", "content_status": "fail", "source": "execution"})
            turn["finished_at"] = now()
            save(path, report)
            print(json.dumps({"agent": scenario["id"], "turn": number, "status": turn["status"]}), flush=True)

    async def run(self):
        self.catalog = await self.get("/api/gateway/catalog")
        snapshot = self.args.output / "catalog-start.json.gz"
        if snapshot.exists():
            previous = read(snapshot)
            for key in ("catalog_version", "ranking_version", "policy_version", "model_routing"):
                if previous[key] != self.catalog[key]:
                    raise ValueError("Production versions changed; do not merge a resumed run")
        else:
            save(snapshot, self.catalog)
        manifest = self.args.output / "manifest.json"
        if manifest.exists() and read(manifest) != self.suite:
            raise ValueError("Frozen suite differs")
        save(manifest, self.suite)
        groups = {}
        independent = []
        for case in self.suite["cases"]:
            if case.get("conversation_group"):
                groups.setdefault(case["conversation_group"], []).append(case)
            else:
                independent.append(case)
        await asyncio.gather(*(self.one(c) for c in independent),
                             *(self.conversation(cases) for cases in groups.values()))
        for scenario in self.suite["industries"]:
            await self.industry(scenario)
        save(self.args.output / "catalog-end.json.gz", await self.get("/api/gateway/catalog"))
        save(self.args.output / "execution-finished.json", {"finished_at": now(), "suite_version": self.suite["version"]})


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--url", default="https://d3eipg2g3l3an3.cloudfront.net")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    save(args.output / "runner-source.json", {"sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    credentials = json.loads(boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId="DeciderModelRouter/admin-credentials")["SecretString"])
    async with httpx.AsyncClient(base_url=args.url, headers={"X-Decider-Client": "web"},
                                 timeout=httpx.Timeout(240, connect=20), trust_env=False) as client:
        response = await client.post("/api/auth/login", json=credentials)
        response.raise_for_status()
        del credentials
        try:
            await Evaluation(args, client).run()
        finally:
            await client.post("/api/auth/logout", json={})


if __name__ == "__main__":
    asyncio.run(main())
