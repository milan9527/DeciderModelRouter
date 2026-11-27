"""Audited fallback; GPU routing also delegates replacement decisions to the GPU."""

import asyncio
import math
import time
from decimal import Decimal

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.gateway_adapter import capabilities
from app.gateway_recovery import (
    COOLDOWN_SECONDS,
    MAX_ATTEMPTS,
    MAX_ENDPOINT_ATTEMPTS,
    MAX_ENDPOINTS,
    MIN_ATTEMPT_SECONDS,
    RETRYABLE,
    TOTAL_TIMEOUT_SECONDS,
    error_details,
    retry_delay,
)
from app.routing_selector import SELECTOR_VERSION


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
    return result[:MAX_ENDPOINTS - 1]


async def execute(request, row, task, workflow, ranking_version, runtime):
    from app.routing import execute_selected

    rows = [row]
    gpu = runtime.selection.get("selection_source") == "gpu_decision_model"
    if runtime.options.fallback and not gpu:
        rows += fallback_rows(row, runtime.selection)
    started = time.perf_counter()
    index, endpoint_attempt, retry_count = 0, 0, 0
    last_error = None
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            for number in range(1, MAX_ATTEMPTS + 1):
                if last_error and TOTAL_TIMEOUT_SECONDS - (time.perf_counter() - started) <= MIN_ATTEMPT_SECONDS:
                    raise last_error
                candidate = rows[index]
                endpoint_attempt += 1
                retry_count += int(endpoint_attempt > 1)
                attempt = {"number": number, "model_id": candidate["model"]["id"],
                           "model_name": candidate["model"]["name"],
                           "configuration_id": candidate["candidate_id"], "status": "started",
                           "endpoint_attempt": endpoint_attempt, "retry_count": retry_count,
                           "fallback_count": index}
                if runtime.send:
                    await runtime.send("attempt", attempt)
                at = time.perf_counter()
                try:
                    result = await execute_selected(
                        request, candidate, task, workflow, "classified", SELECTOR_VERSION, ranking_version,
                    )
                except HTTPException as exc:
                    last_error = exc
                    details = error_details(exc)
                    code = details["error_code"]
                    if details["retry_after_ms"] is not None:
                        exc.headers = {**(exc.headers or {}), "Retry-After":
                                       str(math.ceil(details["retry_after_ms"] / 1000))}
                    attempted = {r["model"]["invoke_id"] for r in rows[:index + 1]}
                    remaining = [r for r in runtime.selection["candidates"]
                                 if r["model"]["invoke_id"] not in attempted and not r.get("adapter")]
                    recoverable = (runtime.options.fallback and code in RETRYABLE
                                   and not runtime.partial_output and not candidate.get("adapter")
                                   and number < MAX_ATTEMPTS)
                    retry = recoverable and endpoint_attempt < MAX_ENDPOINT_ATTEMPTS
                    fallback = bool(recoverable and not retry and index + 1 < MAX_ENDPOINTS
                                    and (remaining if gpu else index + 1 < len(rows)))
                    delay = retry_delay(number, details) if retry or fallback else 0
                    time_left = TOTAL_TIMEOUT_SECONDS - (time.perf_counter() - started)
                    exhausted = (retry or fallback) and delay + MIN_ATTEMPT_SECONDS >= time_left
                    if exhausted:
                        retry = fallback = False
                    outcome = {**attempt, **details, "status": "failed",
                               "elapsed_ms": round((time.perf_counter() - at) * 1000, 3),
                               "will_retry": retry, "will_fallback": fallback,
                               "backoff_ms": round(delay * 1000, 3) if retry or fallback else 0,
                               "recovery_budget_exhausted": bool(exhausted),
                               "partial_output": runtime.partial_output}
                    runtime.attempts.append(outcome)
                    if runtime.send:
                        await runtime.send("attempt", outcome)
                    # A short per-user endpoint cooldown is operational evidence only.
                    # It never changes benchmark scores or invents a success probability.
                    # Only persistent failures cool the endpoint; a recovered retry
                    # must not leave a false cooldown or erase another request's failure.
                    if code in RETRYABLE and not retry:
                        await run_in_threadpool(runtime.history.records.put, runtime.pk,
                                                "HEALTH#" + candidate["model"]["invoke_id"], {
                                                    "error_code": code, "expires_at": math.ceil(time.time()
                                                        + max(COOLDOWN_SECONDS, (details["retry_after_ms"] or 0) / 1000)),
                                                    "invoke_id": candidate["model"]["invoke_id"],
                                                    "run_id": runtime.run_id,
                                                })
                    if not (retry or fallback):
                        raise
                    await asyncio.sleep(delay)
                    if TOTAL_TIMEOUT_SECONDS - (time.perf_counter() - started) <= MIN_ATTEMPT_SECONDS:
                        raise
                    if retry:
                        continue
                    if gpu:
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
                    index += 1
                    endpoint_attempt = 0
                    continue
                outcome = {**attempt, "status": "completed" if result.get("complete") else "incomplete",
                           "elapsed_ms": round((time.perf_counter() - at) * 1000, 3)}
                runtime.attempts.append(outcome)
                if runtime.send:
                    await runtime.send("attempt", outcome)
                return {**result, "model_name": candidate["model"]["name"],
                        "adapter": capabilities(candidate)["adapter"],
                        "configuration_id": candidate["candidate_id"], "attempts": runtime.attempts,
                        "fallback_count": index, "retry_count": retry_count,
                        "backend_total_ms": round((time.perf_counter() - started) * 1000, 3)}
    except TimeoutError as exc:
        raise HTTPException(504, "本次执行达到 180 秒总时限；上游结果可能未知，已停止回退。") from exc
