"""Account quota evidence bound to the exact Bedrock invocation endpoint."""

import math


def quota_exclusion(model, region):
    evidence = model.get("inference_quotas")
    if evidence is None:
        # Unmapped quota is unknown, not zero and not proof of available capacity.
        return None
    if (
        not isinstance(evidence, dict)
        or evidence.get("region") != region
        or evidence.get("invoke_id") != model.get("invoke_id")
        or not evidence.get("checked_at")
        or not isinstance(evidence.get("checks"), list)
        or not evidence["checks"]
    ):
        return "推理配额证据与区域或调用端点不一致，请刷新目录"
    for quota in evidence["checks"]:
        value = quota.get("value") if isinstance(quota, dict) else None
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return "推理配额证据无效，请刷新目录"
        if value == 0:
            return (
                f"账户推理配额为 0：{quota.get('name', '未知配额')} "
                f"（{quota.get('code', '未知代码')}；核验于 {evidence['checked_at']}）。"
                "配额恢复后需刷新目录。"
            )
    return None
