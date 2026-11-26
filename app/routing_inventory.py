"""Explain evidence coverage independently from current routing eligibility."""

from collections import Counter


def inventory(catalog, rankings):
    from app.bedrock_evidence import load_evidence, measured
    from app.public_benchmarks import inventory as public_inventory
    from app.public_benchmarks import load_public_benchmarks

    measured_data = load_evidence()
    public_data = load_public_benchmarks()
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
        public_rows = public_data["records"].get(mid, [])
        if public_rows and status in {"unmapped", "reference_only"}:
            status, reason = "public_reference", "已接入同型号公开成绩；配置与端点差异保留，不等于当前配置实测"
        rows[mid] = {
            "status": status,
            "reason": reason,
            "reference": reference,
            "bedrock_evaluation": measured(model, measured_data),
            "public_benchmarks": public_rows,
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
        "bedrock_measurement_version": measured_data["version"],
        "bedrock_measurement_source": measured_data.get("source"),
        "bedrock_measured_models": sum(row["bedrock_evaluation"].get("status") == "measured" for row in rows.values()),
        "bedrock_blocked_models": sum(row["bedrock_evaluation"].get("status") == "blocked" for row in rows.values()),
        "benchmark_releases": rankings.get("benchmark_releases", []),
        "public_benchmarks": public_inventory(public_data),
    }
