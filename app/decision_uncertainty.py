"""Optional model probabilities for inspection; never change the selected option."""

import math


def choice_answer(raw, criteria):
    result = {"choice": raw["choice"], "distribution_status": "unreported"}
    values = raw.get("probabilities")
    if values is None:
        return result
    result["distribution_status"] = "invalid"
    if not isinstance(values, dict) or set(values) != set(criteria):
        return result
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
           for v in values.values()):
        return result
    total = sum(values.values())
    # Some providers round each probability to four decimal places.
    if total <= 0 or abs(total - 1) > max(1e-6, len(values) * 0.00005 + 1e-6):
        return result
    if values[raw["choice"]] < max(values.values()):
        return result
    return {**result, "distribution_status": "reported", "probabilities": values,
            "confidence": values[raw["choice"]], "probability_sum": total}


def distribution(answer, aliases):
    status = answer.get("distribution_status", "unreported")
    if status != "reported":
        return {"status": status, "note": "未获得有效候选概率；不推算置信度。"}
    values = answer["probabilities"]
    ranked = sorted(values, key=lambda k: -values[k])
    selected = answer["choice"]
    second = max((p for k, p in values.items() if k != selected), default=0)
    return {
        "status": "reported", "selected_probability": values[selected],
        "runner_up_probability": second, "margin": values[selected] - second,
        "candidate_count": len(values), "probability_sum": answer["probability_sum"],
        "probabilities": {aliases[k]["candidate_id"]: p for k, p in values.items() if k in aliases},
        "top_candidates": [
            {"candidate_id": aliases[k]["candidate_id"] if k in aliases else None,
             "option": k, "probability": values[k]} for k in ranked[:5]
        ],
        "note": "仅表示决策模型在本次候选中的选择倾向，不是回答正确率；候选集合不同不可直接比较。",
    }
