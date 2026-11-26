"""Audited fallback; GPU routing also delegates replacement decisions to the GPU."""

import asyncio
import time
from decimal import Decimal

from botocore.exceptions import ClientError
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.gateway_adapter import capabilities
from app.routing_selector import SELECTOR_VERSION

RETRYABLE = {"ThrottlingException", "ServiceUnavailableException", "ModelNotReadyException"}


def fallback_rows(row, selection):
    """Keep the same evidence cohort and bounded cost/quality; hard filters ran upstream."""
    ordered = sorted(selection["candidates"], key=lambda r: (
        r["candidate_id"] not in selection.get("frontier", []),
        r.get("quality_regret", float("inf")), r["candidate_id"],
    ))
    seen, result = {row["model"]["invoke_id"]}, []
    for candidate in ordered:
        if candidate["model"]["invoke_id"] in seen:
            continue
        if candidate.get("adapter") or row.get("adapter"):
            continue  # Native media jobs have different submission/idempotency semantics.
        cost, base = candidate.get("selection_cost_usd"), row.get("selection_cost_usd")
        if cost is None or base is None or Decimal(cost) > Decimal(base) * Decimal("1.25"):
            continue
        if candidate.get("quality_regret", float("inf")) > row.get("quality_regret", 0) + 5:
            continue
        seen.add(candidate["model"]["invoke_id"])
        result.append(candidate)
    return result[:2]


async def execute(request, row, task, workflow, ranking_version, runtime):
    from app.routing import execute_selected

    rows = [row]
    gpu = runtime.selection.get("selection_source") == "gpu_decision_model"
    if runtime.options.fallback and not gpu:
        rows += fallback_rows(row, runtime.selection)
    started = time.perf_counter()
    try:
        async with asyncio.timeout(180):
            for index, candidate in enumerate(rows):
                attempt = {"number": index + 1, "model_id": candidate["model"]["id"],
                           "model_name": candidate["model"]["name"],
                           "configuration_id": candidate["candidate_id"], "status": "started"}
                if runtime.send:
                    await runtime.send("attempt", attempt)
                at = time.perf_counter()
                try:
                    result = await execute_selected(
                        request, candidate, task, workflow, "classified", SELECTOR_VERSION, ranking_version,
                    )
                except HTTPException as exc:
                    cause = exc.__cause__
                    code = cause.response["Error"]["Code"] if isinstance(cause, ClientError) else None
                    retry = (code in RETRYABLE and not runtime.partial_output and (
                        index + 1 < len(rows) or (
                            gpu and runtime.options.fallback and index < 2 and not candidate.get("adapter")
                        )))
                    outcome = {**attempt, "status": "failed", "error_code": code or str(exc.status_code),
                               "elapsed_ms": round((time.perf_counter() - at) * 1000, 3),
                               "will_fallback": retry, "partial_output": runtime.partial_output}
                    runtime.attempts.append(outcome)
                    if runtime.send:
                        await runtime.send("attempt", outcome)
                    # A short per-user endpoint cooldown is operational evidence only.
                    # It never changes benchmark scores or invents a success probability.
                    if code in RETRYABLE:
                        await run_in_threadpool(runtime.history.records.put, runtime.pk,
                                                "HEALTH#" + candidate["model"]["invoke_id"], {
                                                    "error_code": code, "expires_at": int(time.time()) + 60,
                                                    "invoke_id": candidate["model"]["invoke_id"],
                                                    "run_id": runtime.run_id,
                                                })
                    if not retry:
                        raise
                    if gpu:
                        attempted = {r["model"]["invoke_id"] for r in rows}
                        remaining = [r for r in runtime.selection["candidates"]
                                     if r["model"]["invoke_id"] not in attempted and not r.get("adapter")]
                        if not remaining:
                            raise
                        pool = {**runtime.selection, "candidates": remaining,
                                "candidate_count": len(remaining), "selected_candidate_id": None,
                                "reroute_reason": {"error_code": code,
                                                   "excluded_invoke_ids": sorted(attempted)}}
                        selected = await request.app.state.gateway_decision.select(
                            runtime.decision_body, request, pool)
                        replacement = next((r for r in remaining if r["candidate_id"] ==
                                            selected["selected_candidate_id"]), None)
                        if runtime.send:
                            await runtime.send("reroute", {
                                "reason": pool["reroute_reason"],
                                "selected_candidate_id": selected["selected_candidate_id"],
                                "decision_calls": selected.get("decision_calls", []),
                                "model_decision_ms": selected.get("decision_ms"),
                            })
                        if replacement is None:
                            raise
                        rows.append(replacement)
                    continue
                outcome = {**attempt, "status": "completed" if result.get("complete") else "incomplete",
                           "elapsed_ms": round((time.perf_counter() - at) * 1000, 3)}
                runtime.attempts.append(outcome)
                if runtime.send:
                    await runtime.send("attempt", outcome)
                return {**result, "model_name": candidate["model"]["name"],
                        "adapter": capabilities(candidate)["adapter"],
                        "configuration_id": candidate["candidate_id"], "attempts": runtime.attempts,
                        "fallback_count": index,
                        "backend_total_ms": round((time.perf_counter() - started) * 1000, 3)}
    except TimeoutError as exc:
        raise HTTPException(504, "本次执行达到 180 秒总时限；上游结果可能未知，已停止回退。") from exc
