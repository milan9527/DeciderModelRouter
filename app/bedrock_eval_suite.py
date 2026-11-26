"""Shared, visible development tasks; scores describe these tasks, not general accuracy."""

import hashlib
import json

from app.gateway_effect_cases import suite


def evaluation_suite():
    original = {r["id"]: r for r in suite()["items"]}
    groups = {
        "general_text": ["extract-json", "csv", "quoted-injection", "literal-injection"],
        "reasoning": ["probability", "percent", "weighted", "units", "schedule"],
        "coding": ["python-dedup", "python-brackets", "python-merge"],
        "vision": ["image-red", "image-blue", "image-ocr", "image-chart"],
        "audio_understanding": ["asr"],
    }
    rows = [{**original[cid], "workflow": workflow} for workflow, ids in groups.items() for cid in ids]
    rows.append({
        "id": "insurance-calculation", "workflow": "reasoning", "family": "行业核算",
        "task": {"text": "财产险同一事故：票据I1金额18000元，票据I2金额9000元但缺照片，"
                         "再次提交I1金额18000元。票据去重，缺材料部分暂不计入。同一事故免赔额2000元，"
                         "扣免赔后赔90%，总限额30000元。分别计算当前材料和补齐I2后估算赔款。"
                         '只输出JSON {"current":数值,"completed":数值}，不要解释。'},
        "judge": {"kind": "json", "expected": {"current": 14400, "completed": 22500}},
    })
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"version": digest[:24], "sha256": digest, "scope": "visible_development_suite",
            "max_output_tokens": 4096, "cases": rows}
