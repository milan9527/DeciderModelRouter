"""One choice over the whole eligible pool, including execution-failure reroutes."""

import hashlib
import json
from time import perf_counter


async def select_all(service, body, request, selection):
    from app.gateway_joint_decision import joint_payload

    started = perf_counter()
    runtime = getattr(request.state, "gateway_runtime", None)
    rows = selection["candidates"]
    if not rows:
        selection.update(reason=selection.get("execution_notice") or "没有满足本次能力和硬约束的候选。",
                         decision_calls=[], decision_ms=0,
                         stickiness={"enabled": bool(runtime and runtime.options.stickiness),
                                     "retained": False, "reason": "没有满足当前约束的配置。"})
        return selection
    # Shuffle by request hash, not performance/price, and retain the complete audit trail.
    seed = hashlib.sha256(body.task.text.encode()).hexdigest()
    candidates = sorted(rows, key=lambda r: hashlib.sha256((seed + r["candidate_id"]).encode()).hexdigest())
    candidates = [{**r, "workflow": selection["profile"]["workflow"]} for r in candidates]
    state = {"request": body.task.text, "task_profile": selection["profile"]}
    profile_input = {"state": json.dumps(state, ensure_ascii=False), "questions": {}}
    if runtime:
        profile_input = runtime.classify(profile_input)
    data, aliases = joint_payload(body, request, profile_input, candidates)
    result = await service.infer(body, request, data, selection=True)
    choice = result["answers"]["action"]["choice"]
    winner = aliases.get(choice)
    selected = winner["candidate_id"] if winner else None
    calls = [{
        "round": 1, "mode": "single_choice", "candidate_ids": [r["candidate_id"] for r in rows],
        "selected_candidate_id": selected, "choice": choice, "model": result["model"],
        "provider": body.provider,
        "input_hash": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
        "usage": result.get("usage"), "timings": result["timings"], "assessment_count": 1,
    }]
    return finish_selection(body, request, selection, selected, calls,
                            (perf_counter() - started) * 1000, len(rows), mode="single_choice")


def finish_selection(body, request, selection, selected, calls, elapsed, evaluated, *, mode):
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
               "GPU一次推理根据评测证据选择；全部合格配置均已参与判断。" if winner else
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
        runtime.signature = {"policy": selection["routing_version"], "evidence": selection["measurement_version"]}
    return selection
