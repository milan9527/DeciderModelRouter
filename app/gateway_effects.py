"""User-scoped, bounded end-to-end routing evaluations and auditable output reviews."""

import asyncio
import copy
import hashlib
import json
import logging
import secrets
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, model_validator
from starlette.concurrency import run_in_threadpool

from app import routing
from app.gateway_effect_cases import REFERENCE_ERRATA, suite
from app.gateway_effect_judge import OVERALL_METRIC_VERSION, grade, overall_outcome, summarize
from app.gateway_evaluations import Evaluations
from app.gateway_store import clean
from app.routing_balance import preferences
from app.routing_profile import PROFILE_VERSION, RequestProfile
from app.routing_selector import SELECTOR_VERSION, load_calibration
from app.schemas import StrictModel

router = APIRouter(prefix="/api/gateway/effects", tags=["End-to-end effectiveness"])
logger = logging.getLogger(__name__)
ASSETS = Path(__file__).with_name("evaluation_assets")


class CustomCase(StrictModel):
    title: str = Field(min_length=1, max_length=100)
    task: routing.Task
    rubric: str = Field(min_length=1, max_length=1000)
    expected_text: str | None = Field(default=None, max_length=2000)


class EffectBody(StrictModel):
    providers: list[Literal["decider", "liquid", "h2o"]] = Field(min_length=1, max_length=3)
    case_ids: list[str] = Field(default_factory=list, max_length=96)
    custom_cases: list[CustomCase] = Field(default_factory=list, max_length=8)
    baselines: bool = False
    max_target_calls: int = Field(default=96, ge=0, le=192, strict=True)
    max_output_tokens: int = Field(default=16384, ge=128, le=16384, strict=True)

    @model_validator(mode="after")
    def bounded(self):
        if not 1 <= len(self.case_ids) + len(self.custom_cases) <= 96:
            raise ValueError("每批需要1至96个场景，可分批扩展")
        if len(set(self.providers)) != len(self.providers) or len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("决策模型或场景不能重复")
        return self


class Review(StrictModel):
    status: Literal["pass", "fail"]
    reason: str = Field(min_length=10, max_length=4000)
    method: Literal["user_review", "assistant_review", "isolated_tests"] = "user_review"


def execution_key(row, task, workflow):
    """Deduplicate only identical effective requests within this case and job."""
    request = {
        "invoke_id": row["model"]["invoke_id"], "workflow": workflow,
        "adapter": row.get("adapter"),
        "request_fields": row["benchmark"].get("request_fields", {}),
        "inference_config": row["benchmark"].get("inference_config", {}),
        "task": task.model_dump(mode="json", exclude={"routing_preferences"}),
        "max_output_tokens": preferences(task).max_output_tokens,
    }
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


class EffectEvaluations(Evaluations):
    def save(self, user, job):
        job["updated_at"] = int(time.time() * 1000)
        self.history.records.put(self.key(user), "EFFECT#" + job["id"], job)

    def header(self, user, job_id):
        job = self.history.records.get(self.key(user), "EFFECT#" + job_id)
        if not job:
            raise HTTPException(404, "效果评测不存在")
        if job["status"] == "running" and job["updated_at"] < time.time() * 1000 - 300_000:
            job.update(status="interrupted", note="执行中断，已开始但未记录结果的调用状态未知；不自动重跑。")
            self.save(user, job)
        return job

    def rows(self, user, job_id):
        rows, cursor = [], None
        while True:
            page = self.history.records.query(
                self.key(user), "EROW#" + job_id + "#", limit=1000, reverse=False, cursor=cursor,
            )
            rows.extend(page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                return rows

    def get(self, user, job_id):
        job = self.header(user, job_id)
        job["results"] = self.rows(user, job_id)
        job["summary"] = summarize(job["results"], job["providers"], len(job["cases"]))
        job["overall_metric_version"] = OVERALL_METRIC_VERSION
        job["results"] = [{**row, "overall": overall_outcome(row)} for row in job["results"]]
        job["reference_errata"] = [
            entry for entry in REFERENCE_ERRATA if entry["affected_version"] == job["dataset_version"]
        ]
        return job

    def listing(self, user):
        items, cursor = [], None
        while True:
            page = self.history.records.query(self.key(user), "EFFECT#", limit=100, cursor=cursor)
            items.extend(
                {k: v for k, v in self.header(user, j["id"]).items() if k != "cases"}
                for j in page["items"]
            )
            cursor = page["next_cursor"]
            if not cursor:
                break
        # Job keys are random IDs, not timestamps. Sort before selecting the latest 100.
        items.sort(key=lambda j: j["created_at"], reverse=True)
        return {"items": items[:100], "next_cursor": None, "older_jobs": max(0, len(items) - 100)}

    async def start(self, body, request):
        user = request.state.user["username"]
        catalog = suite()
        by_id = {c["id"]: c for c in catalog["items"]}
        if any(cid not in by_id for cid in body.case_ids):
            raise HTTPException(422, "未知场景，请刷新场景目录")
        cases = [by_id[cid] for cid in body.case_ids]
        for i, c in enumerate(body.custom_cases):
            await run_in_threadpool(request.app.state.media.resolve, user, c.task.attachments)
            cases.append({
                "id": f"custom-{i + 1}", "title": c.title, "family": "自定义",
                "task": c.task.model_dump(mode="json", exclude_unset=True), "fixture": None, "gap": None,
                "allowed_workflows": [],
                "judge": {"kind": "exact" if c.expected_text is not None else "manual",
                          "expected": c.expected_text, "rubric": c.rubric},
            })
        for provider in body.providers:
            self.decision.service(provider)
        async with self.lock:
            if sum(not t.done() for t in self.tasks.values()) >= 2:
                raise HTTPException(429, "已有两批效果评测运行中")
            # This check also survives an ECS replacement while the old worker is draining.
            existing = await run_in_threadpool(self.listing, user)
            if any(j["status"] == "running" for j in existing["items"]):
                raise HTTPException(409, "此账户已有运行中的效果评测")
            job = {
                "id": secrets.token_hex(12), "created_at": int(time.time() * 1000),
                "status": "running", "providers": body.providers, "cases": cases,
                "dataset_version": catalog["version"], "dataset_scope": catalog["scope"],
                "judge_version": catalog["judge_version"], "profile_version": PROFILE_VERSION,
                "catalog_version": routing.load_catalog()["version"],
                "ranking_version": routing.load_rankings()["version"],
                "policy_version": SELECTOR_VERSION,
                "calibration_version": load_calibration()["version"],
                "baselines": body.baselines, "max_target_calls": body.max_target_calls,
                "max_output_tokens": body.max_output_tokens,
                "planned_gpu_calls": len([c for c in cases if not c["gap"]]) * len(body.providers),
                "started_gpu_calls": 0, "target_model_calls": 0, "reused_observations": 0,
                "started_model_selection_calls": 0, "total_gpu_calls": 0,
                "gpu_call_accounting": "classification_and_selection_v1",
                "recorded_rows": 0, "summary": [],
                "note": (
                    "开发回归集；缺口和待人工评审单独计数。低价/高能力基线使用同一实际分类及原规则策略，"
                    "不是全模型最优解。同用例同有效请求只调用目标一次，共享结果不算独立重复实验。"
                    "顺序执行，目标计数为调用尝试，不自动重试；参考价格不是AWS账单，未知价格不记为0。"
                ),
            }
            await run_in_threadpool(self.save, user, job)
            task = asyncio.create_task(self.work(user, job, request))
            self.tasks[user, job["id"]] = task
            task.add_done_callback(lambda _: self.tasks.pop((user, job["id"]), None))
            return copy.deepcopy(job)

    async def prepare(self, case, request, cap):
        data = copy.deepcopy(case["task"])
        opts = data.setdefault("routing_preferences", {})
        if opts is None:
            opts = data["routing_preferences"] = {}
        opts["max_output_tokens"] = min(opts.get("max_output_tokens", cap), cap)
        opts["expected_output_tokens"] = min(opts.get("expected_output_tokens", 128), opts["max_output_tokens"])
        if case["fixture"]:
            name = case["fixture"]
            mime = {".png": "image/png", ".wav": "audio/wav", ".mp4": "video/mp4"}[Path(name).suffix]
            raw = (ASSETS / name).read_bytes()
            asset = await run_in_threadpool(request.app.state.media.put, request.state.user["username"], raw, mime)
            data["attachments"] = [{"id": asset["id"], "kind": asset["kind"]}]
        return routing.Task(**data)

    async def append(self, user, job, rows, row):
        case = next(c for c in job["cases"] if c["id"] == row["case_id"])
        if case.get("expected_boundary_statuses") and row["arm"] == "routed":
            row["boundary_check"] = row["status"] in case["expected_boundary_statuses"]
        row["id"] = f"{len(rows):04d}"
        row["recorded_at"] = int(time.time() * 1000)
        row = clean(row)
        await run_in_threadpool(
            self.history.records.put, self.key(user), "EROW#" + job["id"] + "#" + row["id"], row
        )
        rows.append(row)
        job["recorded_rows"] = len(rows)
        job["summary"] = summarize(rows, job["providers"], len(job["cases"]))
        await run_in_threadpool(self.save, user, job)

    async def observe(self, user, job, request, case, row, candidate, task, workflow, cache, token=None):
        key = execution_key(candidate, task, workflow)
        if key in cache:
            job["reused_observations"] += 1
            row.update(copy.deepcopy(cache[key]), reused=True)
            return
        if job["target_model_calls"] >= job["max_target_calls"]:
            row.update(status="budget_exhausted", grade={"status": "unmeasured", "reason": "达到目标调用上限"})
            return
        job["target_model_calls"] += 1
        await run_in_threadpool(self.save, user, job)  # Record attempt before invoking upstream.
        observation = {"observation_id": secrets.token_hex(12), "reused": False}
        try:
            if token:
                output = await routing.auto_execute(routing.AutoExecute(route_token=token), request)
            else:
                output = await routing.execute_selected(
                    request, candidate, task, workflow, "classified", SELECTOR_VERSION, job["ranking_version"],
                )
            observation.update(status="completed" if output.get("complete") else "incomplete",
                               output=output, grade=grade(case, output))
        except HTTPException as exc:
            observation.update(
                status="error", error={"code": exc.status_code, "message": exc.detail},
                grade={"status": "fail", "source": "execution", "reason": "目标调用失败，任务未完成"},
            )
        cache[key] = copy.deepcopy(observation)
        row.update(observation)

    async def run_case(self, user, job, request, case, task, provider, cache, rows):
        row = {
            "case_id": case["id"], "provider": provider, "arm": "routed",
            "status": "error", "task": task.model_dump(mode="json"),
            "grade": {"status": "unmeasured"},
        }
        job["started_gpu_calls"] += 1
        await run_in_threadpool(self.save, user, job)

        async def counted_infer(body, request, payload, *, selection=False):
            job["total_gpu_calls"] += 1
            job["started_model_selection_calls"] += int(selection)
            await run_in_threadpool(self.save, user, job)
            return await self.decision.infer(body, request, payload, selection=selection)

        async def counted_select(body, request, pool):
            from app.gateway_model_decision import select_all

            return await select_all(SimpleNamespace(infer=counted_infer), body, request, pool)

        try:
            body = routing.AutoRoute(
                task=task, provider=provider, mode="live",
                **{k: job[k] for k in ("catalog_version", "ranking_version", "policy_version")},
            )
            route = await routing.run_classified_route(body, request, infer=counted_infer,
                                                      model_select=counted_select)
            flow, step = route["steps"]
            selection = step["selection"]
            candidate = next((c for c in selection["candidates"] if c["candidate_id"] == step["choice"]), None)
            row.update(
                route_status=route["status"], route_ms=route["route_total_ms"],
                profile=flow["profile"], assessed_profile=flow["assessed_profile"],
                classifier_model=flow["model"], classifier_timings=flow["timings"],
                classifier_usage=flow["usage"], classifier_deployment=flow.get("deployment"),
                input_hash=flow["input_hash"], configuration_id=step["choice"],
                selected_model=step["selected_model"], candidate_count=step["candidate_count"],
                model_decision_ms=selection.get("decision_ms"),
                model_decision_calls=selection.get("decision_calls", []),
                measurement_version=selection.get("measurement_version"),
                selection_reason=step["notice"], cost_context=selection.get("cost_context"),
                workflow_fit=flow["profile"]["workflow"] in case["allowed_workflows"] if case["allowed_workflows"] else None,
            )
            if not candidate:
                row.update(status="no_route", grade={"status": "unmeasured", "reason": step["notice"]})
                await self.append(user, job, rows, row)
                return
            profile = RequestProfile(**flow["profile"])
            if not selection.get("execution_ready", True) or profile.tools == "yes" or profile.structured_output == "yes":
                row.update(status="execution_blocked", grade={"status": "unmeasured",
                           "reason": selection.get("execution_notice") or "工具或强制结构化执行参数尚未接入"})
                await self.append(user, job, rows, row)
                return
            await self.observe(user, job, request, case, row, candidate, task, profile.workflow, cache, step["route_token"])
            await self.append(user, job, rows, row)
            if job["baselines"] and profile.workflow in {"general_text", "customer_support", "coding", "reasoning"}:
                for tier in ("low", "high"):
                    baseline_task = task.model_copy(update={"routing_preferences": preferences(task).model_copy(update={"cost_tier": tier})})
                    selected = routing.auto_selection(baseline_task, profile, routing.load_catalog(), routing.load_rankings(), load_calibration())
                    chosen = next((c for c in selected["candidates"] if c["candidate_id"] == selected["selected_candidate_id"]), None)
                    baseline = {
                        "case_id": case["id"], "provider": provider, "arm": tier,
                        "status": "no_route", "grade": {"status": "unmeasured"},
                        "configuration_id": chosen["candidate_id"] if chosen else None,
                        "selected_model": chosen["model"] if chosen else None,
                        "selection_reason": selected["reason"], "profile": profile.model_dump(),
                    }
                    if chosen:
                        await self.observe(user, job, request, case, baseline, chosen, baseline_task, profile.workflow, cache)
                    await self.append(user, job, rows, baseline)
        except HTTPException as exc:
            row.update(status="error", error={"code": exc.status_code, "message": exc.detail},
                       grade={"status": "fail", "source": "routing", "reason": "路由调用失败，任务未完成"})
            await self.append(user, job, rows, row)

    async def work(self, user, job, request):
        rows = []
        try:
            for case in job["cases"]:
                if case["gap"]:
                    for provider in job["providers"]:
                        await self.append(user, job, rows, {
                            "case_id": case["id"], "provider": provider, "arm": "routed",
                            "status": "capability_gap", "grade": {"status": "unmeasured", "reason": case["gap"]},
                        })
                    continue
                task = await self.prepare(case, request, job["max_output_tokens"])
                cache = {}  # Never reuse another case's output, including identical-looking prompts.
                for provider in job["providers"]:
                    await self.run_case(user, job, request, case, task, provider, cache, rows)
            job["status"] = "completed"
        except asyncio.CancelledError:
            job.update(status="interrupted", note="评测已中断；保留已记录结果，已开始但未记录结果的上游调用状态未知，不自动重跑。")
        except Exception:
            logger.exception("Effect evaluation failed: %s", job["id"])
            job.update(status="error", note="评测执行异常；已记录结果保留，未自动重跑。")
        finally:
            job["summary"] = summarize(rows, job["providers"], len(job["cases"]))
            await run_in_threadpool(self.save, user, job)

    async def cancel(self, user, job_id):
        self.header(user, job_id)
        task = self.tasks.get((user, job_id))
        if task and not task.done():
            task.cancel()
            await task
        elif self.header(user, job_id)["status"] == "running":
            raise HTTPException(409, "任务在另一服务进程中运行；本次未发出停止信号，请稍后查看")
        return await run_in_threadpool(self.get, user, job_id)

    def review(self, user, job_id, row_id, body):
        job = self.header(user, job_id)
        if job["status"] == "running":
            raise HTTPException(409, "请等待评测结束后评分")
        key = "EROW#" + job_id + "#" + row_id
        row = self.history.records.get(self.key(user), key)
        if not row:
            raise HTTPException(404, "评测记录不存在")
        if not row.get("output", {}).get("complete"):
            raise HTTPException(422, "只能对已完成的目标输出评审；缺口或调用失败不能人工改为通过")
        if row.get("review"):
            raise HTTPException(409, "已有评审；保留原始记录，请新建重复评测")
        row["review"] = {**body.model_dump(), "source": "account_review", "reviewer": user,
                         "created_at": int(time.time() * 1000)}
        self.history.records.put(self.key(user), key, row)
        # A shared observation has identical task, configuration and output. Apply its review
        # to every reference so deduplication does not bias providers/baselines.
        for other in self.rows(user, job_id):
            if other["id"] != row_id and other.get("observation_id") == row.get("observation_id") and not other.get("review"):
                other["review"] = {**row["review"], "shared_from_row": row_id}
                self.history.records.put(self.key(user), "EROW#" + job_id + "#" + other["id"], other)
        job["summary"] = summarize(self.rows(user, job_id), job["providers"], len(job["cases"]))
        self.save(user, job)
        return row


@router.get("/cases")
def cases():
    return suite()


@router.get("")
def listing(request: Request):
    return request.app.state.effects.listing(request.state.user["username"])


@router.post("", status_code=202)
async def start(body: EffectBody, request: Request):
    return await request.app.state.effects.start(body, request)


@router.get("/{job_id}")
def detail(job_id: str, request: Request):
    return request.app.state.effects.get(request.state.user["username"], job_id)


@router.post("/{job_id}/cancel")
async def cancel(job_id: str, request: Request):
    return await request.app.state.effects.cancel(request.state.user["username"], job_id)


@router.post("/{job_id}/reviews/{row_id}")
def review(job_id: str, row_id: str, body: Review, request: Request):
    return request.app.state.effects.review(request.state.user["username"], job_id, row_id, body)
