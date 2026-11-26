import json


def gpu_answers(payload, classification, *, choose_other=False):
    """Test transport chooses from the actual supplied model cards, never a production ranker."""
    if "action" not in payload["questions"]:
        return {k: {"choice": v} for k, v in classification.items()}
    cards = [(key, json.loads(value)) for key, value in
             payload["questions"]["action"]["criteria"].items() if key != "none"]
    cards.sort(key=lambda pair: (
        not pair[1]["previous_successful_model"],
        pair[1]["model"] != "Nova Micro",
        pair[1]["model"], json.dumps(pair[1]["configuration"], sort_keys=True),
    ))
    if choose_other:
        cards = [p for p in cards if not p[1]["previous_successful_model"]] or cards
    return {"action": {"choice": cards[0][0]}}
