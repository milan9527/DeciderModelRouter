"""Explain evidence coverage independently from current routing eligibility."""

from collections import Counter


def inventory(catalog, rankings):
    rows = {}
    references = rankings.get("model_references", {})
    for model in catalog["models"]:
        mid = model["id"]
        entry, reference = rankings["entries"].get(mid), references.get(mid)
        configurations = [entry, *entry.get("configurations", [])] if entry else []
        if configurations:
            verified = any(
                c.get("configuration_status", "verified") == "verified" for c in configurations
            )
            status = "benchmarked" if verified else "configuration_pending"
            reason = "已映射精确型号及评测配置" if verified else entry["configuration_note"]
        elif reference:
            status, reason = "reference_only", "已核验外部型号资料及参考价；所接入评测无该型号成绩"
        elif mid.count(":") > 1:
            status, reason = "capacity_variant", "容量变体未独立评测，不继承基础型号分数"
        elif (
            "EMBEDDING" in model["output_modalities"]
            or "TEXT" not in model["output_modalities"]
            or any(s in mid for s in ("sonic", "voxtral", "rerank", "safeguard", "pegasus"))
        ):
            status, reason = "specialized", "需要对应模态或专用任务评测，不能使用通用文本分数"
        else:
            status, reason = "unmapped", "已检索接入的公开评测批次，未找到精确型号成绩"
        rows[mid] = {
            "status": status,
            "reason": reason,
            "reference": reference,
            "configurations": [
                {
                    key: c.get(key)
                    for key in (
                        "source",
                        "source_url",
                        "source_id",
                        "release",
                        "dataset_revision",
                        "benchmark_configuration",
                        "configuration_status",
                        "configuration_note",
                        "configuration_source",
                        "category_scores",
                        "subsets",
                        "pricing_source",
                    )
                }
                for c in configurations
            ],
        }
    return {
        "models": rows,
        "counts": dict(Counter(row["status"] for row in rows.values())),
        "mapped_models": sum(bool(row["configurations"]) for row in rows.values()),
        "total_models": len(rows),
        "captured_at": rankings.get("captured_at"),
        "benchmark_releases": rankings.get("benchmark_releases", []),
    }
