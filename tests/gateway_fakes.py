import json


def gpu_answers(payload, classification, *, choose_other=False):
    """Test transport chooses from the actual supplied model cards, never a production ranker."""
    if "action" not in payload["questions"]:
        return {k: {"choice": v} for k, v in classification.items()}
    from app.gateway_joint_decision import WORKFLOW_CODES
    import re

    cards = [(key, {"model": value.split(";")[0], "card": value,
                    "previous": value.endswith(";previous")}) for key, value in
             payload["questions"]["action"]["criteria"].items() if key != "none"]
    workflow = WORKFLOW_CODES[classification["workflow"]]
    state = json.loads(payload["state"])
    if state.get("attachments") and classification["workflow"] not in {"embedding", "rerank"}:
        workflow = {"image": "i", "video": "v", "audio": "a"}[state["attachments"][0]["kind"]]
    compatible = [p for p in cards if workflow in re.search(r";w=([^;]+)", p[1]["card"])[1]]
    cards = compatible or cards
    cards.sort(key=lambda pair: (
        not pair[1]["previous"],
        pair[1]["model"] != "Nova Micro",
        pair[1]["model"],
    ))
    if choose_other:
        cards = [p for p in cards if not p[1]["previous"]] or cards
    return {**{k: {"choice": v} for k, v in classification.items() if k in payload["questions"]},
            "action": {"choice": cards[0][0]}}
