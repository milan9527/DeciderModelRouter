"""Persisted classifier comparisons against a frozen, visible reference task set."""

import asyncio
import contextlib
import secrets
import time
from time import perf_counter
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from app.gateway_store import percentile
from app.gateway_templates import evaluation_cases, load_templates
from app.routing import Task
from app.routing_profile import parse_profile, profile_payload
from app.schemas import StrictModel

router = APIRouter(prefix="/api/gateway/evaluations", tags=["Classifier evaluations"])


class EvaluationBody(StrictModel):
    providers: list[str] = Field(min_length=1, max_length=3)
    case_ids: list[str] = Field(min_length=1, max_length=16)


class Evaluations:
    def __init__(self, history, decision):
        self.history, self.decision = history, decision
        self.tasks = {}
        self.lock = asyncio.Lock()

    def key(self, user):
        return "USER#" + user

    def save(self, user, job):
        job["updated_at"] = int(time.time() * 1000)
        self.history.records.put(self.key(user), "EVAL#" + job["id"], job)

    def get(self, user, job_id):
        item = self.history.records.get(self.key(user), "EVAL#" + job_id)
        if not item:
            raise HTTPException(404, "评测不存在")
        if item["status"] == "running" and item["updated_at"] < time.time() * 1000 - 180_000:
            item.update(status="interrupted", note="评测执行中断；保留已返回样本，未自动重跑。")
            self.save(user, item)
        return item

    def listing(self, user):
        page = self.history.records.query(self.key(user), "EVAL#", limit=100)
        page["items"] = [
            {k: v for k, v in self.get(user, item["id"]).items() if k not in ("results", "cases")}
            for item in page["items"]
        ]
        page["items"].sort(key=lambda r: r["created_at"], reverse=True)
        return page

    async def start(self, body, request):
        user = request.state.user["username"]
        cases = {c["id"]: c for c in evaluation_cases()}
        if len(set(body.providers)) != len(body.providers) or len(set(body.case_ids)) != len(
            body.case_ids
        ):
            raise HTTPException(422, "不能重复选取决策模型或任务")
        if any(p not in self.decision.services for p in body.providers):
            raise HTTPException(422, "决策模型不在允许列表")
        if any(c not in cases for c in body.case_ids):
            raise HTTPException(422, "任务不在已冻结的参考集")
        for provider in body.providers:
            self.decision.service(provider)
        async with self.lock:
            if sum(not t.done() for t in self.tasks.values()) >= 2:
                raise HTTPException(429, "已有评测正在运行，请稍后提交")
            if any(owner == user and not task.done() for (owner, _), task in self.tasks.items()):
                raise HTTPException(409, "当前账户已有评测运行中")
            job = {
                "id": secrets.token_hex(12),
                "created_at": int(time.time() * 1000),
                "status": "running",
                "providers": body.providers,
                "case_ids": body.case_ids,
                "dataset_version": load_templates()["version"],
                "dataset_scope": "visible_development_reference_not_heldout",
                "cases": [
                    {k: cases[c][k] for k in ("id", "title", "task", "expected_profile")}
                    for c in body.case_ids
                ],
                "planned_gpu_calls": len(body.providers) * len(body.case_ids),
                "started_gpu_calls": 0,
                "target_model_calls": 0,
                "results": [],
                "summary": [],
                "note": "测量七项分类与参考标签的一致性，不代表目标模型业务正确率。失败保留在分母中；不自动训练或发布校准。",
            }
            await run_in_threadpool(self.save, user, job)
            task = asyncio.create_task(self.work(user, job, request))
            self.tasks[user, job["id"]] = task
            task.add_done_callback(lambda _: self.tasks.pop((user, job["id"]), None))
            return job

    def summarize(self, job):
        summaries = []
        for provider in job["providers"]:
            rows = [r for r in job["results"] if r["provider"] == provider]
            fields = list(job["cases"][0]["expected_profile"])
            summaries.append(
                {
                    "provider": provider,
                    "samples": len(rows),
                    "planned_samples": len(job["case_ids"]),
                    "completed_calls": sum(r["status"] == "completed" for r in rows),
                    "failed_calls": sum(r["status"] != "completed" for r in rows),
                    "exact_matches": sum(r.get("exact_match", False) for r in rows),
                    "exact_match_rate": sum(r.get("exact_match", False) for r in rows) / len(rows)
                    if rows
                    else None,
                    "field_accuracy": {
                        field: sum(r.get("matches", {}).get(field, False) for r in rows) / len(rows)
                        if rows
                        else None
                        for field in fields
                    },
                    "http_p50_ms": percentile([r.get("http_ms") for r in rows], 0.5),
                    "http_p95_ms": percentile([r.get("http_ms") for r in rows], 0.95),
                    "latency_samples": sum(r.get("http_ms") is not None for r in rows),
                }
            )
        job["summary"] = summaries

    async def work(self, user, job, request):
        try:
            for case in job["cases"]:
                for provider in job["providers"]:
                    row = {
                        "case_id": case["id"],
                        "provider": provider,
                        "expected": case["expected_profile"],
                        "status": "failed",
                    }
                    job["started_gpu_calls"] += 1
                    await run_in_threadpool(self.save, user, job)
                    started = perf_counter()
                    cancelled = False
                    try:
                        result = await self.decision.infer(
                            SimpleNamespace(provider=provider),
                            request,
                            profile_payload(Task(text=case["task"])),
                        )
                        profile = parse_profile(result).model_dump()
                        matches = {k: v == profile[k] for k, v in case["expected_profile"].items()}
                        row.update(
                            status="completed",
                            actual=profile,
                            matches=matches,
                            exact_match=all(matches.values()),
                            model=result["model"],
                            gpu_http_ms=result["timings"]["upstream_ms"],
                            usage=result.get("usage"),
                        )
                    except HTTPException as exc:
                        row.update(error={"code": exc.status_code, "message": exc.detail})
                    except asyncio.CancelledError:
                        cancelled = True
                        row.update(
                            status="interrupted",
                            error={
                                "code": 499,
                                "message": "评测已中断，本次上游执行结果未知",
                            },
                        )
                    row["http_ms"] = round((perf_counter() - started) * 1000, 3)
                    row["timing_scope"] = "API decision call including queue and validation"
                    job["results"].append(row)
                    self.summarize(job)
                    await run_in_threadpool(self.save, user, job)
                    if cancelled:
                        raise asyncio.CancelledError()
            job["status"] = "completed"
        except asyncio.CancelledError:
            job["status"] = "interrupted"
        except Exception:
            job.update(status="error", note="评测服务异常；保留已记录结果，未自动重跑。")
        finally:
            self.summarize(job)
            await run_in_threadpool(self.save, user, job)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


@router.get("/cases")
def cases():
    return {
        "version": load_templates()["version"],
        "items": evaluation_cases(),
        "scope": "公开开发参考集；七项分类逐项评分，不作为独立业务验证集。",
    }


@router.get("")
def listing(request: Request):
    return request.app.state.evaluations.listing(request.state.user["username"])


@router.post("", status_code=202)
async def start(body: EvaluationBody, request: Request):
    return await request.app.state.evaluations.start(body, request)


@router.get("/{job_id}")
def detail(job_id: str, request: Request):
    return request.app.state.evaluations.get(request.state.user["username"], job_id)
