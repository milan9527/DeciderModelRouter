"""One choice over the whole eligible pool, including execution-failure reroutes."""

import hashlib
import json
from time import perf_counter


async def select_all(service, body, request, selection):
    from app.gateway_joint_decision import joint_payload
    from app.routing_quality import accepted, attach_quality

    started = perf_counter()
    runtime = getattr(request.state, "gateway_runtime", None)
    rows = selection["candidates"]
    assessment = selection.pop("assessment", None)
    profile_input = selection.pop("profile_input", None)
    calls = [assessment] if assessment else []
    assessment_ms = (assessment.get("timings") or {}).get("backend_total_ms") if assessment else None
    mode = "two_stage" if assessment else "single_choice"
    if not rows:
        selection.update(reason=selection.get("execution_notice") or "没有满足本次能力和硬约束的候选。",
                         decision_calls=calls, decision_ms=assessment_ms or 0,
                         assessment_ms=assessment_ms, selection_ms=0, decision_mode=mode,
                         stickiness={"enabled": bool(runtime and runtime.options.stickiness),
                                     "retained": False, "reason": "没有满足当前约束的配置。"})
        return selection
    policy = attach_quality(selection, body.task, agent=bool(getattr(runtime, "agent_tools", False)))
    # Shuffle by request hash, not performance/price, and retain the complete audit trail.
    seed = hashlib.sha256(body.task.text.encode()).hexdigest()
    candidates = sorted(rows, key=lambda r: hashlib.sha256((seed + r["candidate_id"]).encode()).hexdigest())
    candidates = [{**r, "workflow": selection["profile"]["workflow"]} for r in candidates]
    state = json.loads(profile_input["state"]) if profile_input else {"request": body.task.text}
    state["task_profile"] = selection["profile"]
    state["quality_policy"] = {k: policy[k] for k in ("version", "active", "comparison", "note")}
    if selection.get("reroute_reason"):
        state["execution_failure"] = selection["reroute_reason"]
    profile_input = {"state": json.dumps(state, ensure_ascii=False), "questions": {}}
    if runtime:
        profile_input = runtime.classify(profile_input)
    selected = None
    for attempt in range(2):
        if not candidates:
            break
        data, aliases = joint_payload(body, request, profile_input, candidates, assessed=True)
        result = await service.infer(body, request, data, selection=True)
        choice = result["answers"]["action"]["choice"]
        winner = aliases.get(choice)
        proposed = winner["candidate_id"] if winner else None
        valid = accepted(policy, proposed)
        calls.append({
            "round": len(calls) + 1, "mode": "selection" if attempt == 0 else "quality_reselection",
            "candidate_ids": [r["candidate_id"] for r in candidates],
            "selected_candidate_id": proposed, "choice": choice, "model": result["model"],
            "provider": body.provider,
            "input_hash": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
            "usage": result.get("usage"), "timings": result["timings"], "assessment_count": 1,
            "candidate_model_ids": {r["candidate_id"]: r["model"]["id"] for r in candidates},
            "task_profile": selection["profile"],
            "quality_check": {"accepted": valid, "policy_version": policy["version"],
                              "reason": policy["assessments"].get(proposed, {}).get("reason")},
            "public_benchmark_version": selection.get("public_benchmark_version"),
            "quality_feedback_version": selection.get("quality_feedback_version"),
            "public_evidence_records": {
                r["candidate_id"]: [e["id"] for e in r.get("public_benchmarks", [])] for r in candidates
            },
        })
        if valid:
            selected = proposed
            break
        # The first call saw every eligible configuration. One explicit repair
        # call may choose among policy-compliant configurations, never a CPU winner.
        if not policy["active"] or proposed is None:
            break
        candidates = [r for r in candidates if accepted(policy, r["candidate_id"])]
        state = json.loads(profile_input["state"])
        state["quality_reselection"] = {
            "rejected_candidate": proposed,
            "instruction": "The previous choice violated QUALITY FIRST. Choose from the remaining compliant models.",
        }
        profile_input = {**profile_input, "state": json.dumps(state, ensure_ascii=False)}
    selection["quality_reselection_count"] = sum(c.get("mode") == "quality_reselection" for c in calls)
    if selected is None and policy["active"]:
        selection.update(
            decision_rejection="quality_requirement_not_met", execution_ready=False,
            reason="GPU未选出符合本次质量要求的配置，已停止执行；没有静默降级。",
            execution_notice="能力优先校验未通过，请查看质量依据与重选记录。",
        )
    selection_ms = (perf_counter() - started) * 1000
    selection.update(assessment_ms=assessment_ms, selection_ms=round(selection_ms, 3))
    return finish_selection(body, request, selection, selected, calls,
                            (assessment_ms or 0) + selection_ms, len(rows), mode=mode)


def finish_selection(body, request, selection, selected, calls, elapsed, evaluated, *, mode):
    from app.routing_quality import explanation
    runtime = getattr(request.state, "gateway_runtime", None)
    previous = None
    if runtime and runtime.options.stickiness and runtime.context["history_turns"]:
        previous = (runtime.previous or {}).get("configuration_id")
    retained = bool(previous and previous == selected)
    winner = next((r for r in selection["candidates"] if r["candidate_id"] == selected), None)
    selection.update(
        selected_candidate_id=selected,
        selected_benchmark=winner["benchmark"] if winner else None,
        reason=selection["reason"] if selection.get("decision_rejection") else
               explanation(selection, selected) if winner else
               "GPU决策模型判断当前候选证据不足或不适合此任务，未强制选模。",
        decision_calls=calls, decision_model=calls[-1]["model"],
        decision_ms=round(elapsed, 3),
        decision_mode=mode,
        evaluated_configurations=evaluated,
        stickiness={"enabled": bool(runtime and runtime.options.stickiness),
                    "retained": retained, "previous_configuration_id": previous,
                    "reason": "决策模型选择沿用上次配置。" if retained else "本轮由决策模型重新选择。"},
    )
    if runtime:
        runtime.decision_body = body
        runtime.selection = selection
        runtime.signature = {"policy": selection["routing_version"], "evidence": selection["measurement_version"],
                             "public_evidence": selection.get("public_benchmark_version"),
                             "quality_feedback": selection.get("quality_feedback_version")}
    return selection
