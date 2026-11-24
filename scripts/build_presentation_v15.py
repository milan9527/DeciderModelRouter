"""Append recorded original-routing comparisons without changing optimization slides."""

import json
import re
from datetime import UTC, datetime
from statistics import mean

import build_presentation_v10 as V
import presentation_layout as L
from build_presentation_v5 import replace_text
from pptx import Presentation
from pptx.util import Inches

ROOT = V.ROOT
OUT = ROOT / "docs/presentations/v15"
BASE = next((ROOT / "docs/presentations/v14").glob("*.pptx"))
DATA = ROOT / "docs/results/model-evidence-2026-10-16"
NAME = "Strands-Decider-v15-原始客服路由与选项顺序诊断-32页-20261016"
PROVIDERS = ("decider", "jev", "bedrock")


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def support_evidence(raw, summary):
    task = raw["runs"][0]["cases"]["support"]["task"]["text"]
    comparison = {"task": task, "strategy": "original", "routes": {}}
    for provider in PROVIDERS:
        samples = [run["cases"]["support"]["sides"][provider] for run in raw["runs"]]
        assert len(samples) == 3
        for sample in samples:
            assert sample["error"] is None
            assert sample["steps"][0]["choice"] == "customer_support"
            assert all(step["source"] == "live" for step in sample["steps"])
            assert sample["steps"][1]["strategy"] == "original"
        step = samples[0]["steps"][1]
        selected = step["selected_model"]
        assert all(s["steps"][1]["choice"] == step["choice"] for s in samples)
        times = [sum(s["timings"]["backend_total_ms"] for s in x["steps"]) for x in samples]
        route_ms = mean(times)
        recorded = summary["cases"]["support"][provider]
        assert abs(route_ms - recorded["original_route"]["mean_ms"]) < 1e-6
        target = recorded["original_execution"]["result"]
        call = next(
            c for c in raw["calls"]
            if c["path"].endswith("/execute")
            and c["request"].get("strategy") == "original"
            and c["request"]["task"]["text"] == task
            and c["request"]["model_id"] == selected["id"]
        )
        assert call["status"] == (504 if provider == "decider" else 200)
        if provider != "decider":
            assert call["response"]["text"] == target["text"]
            assert target["complete"] and target["strategy"] == "original"
        comparison["routes"][provider] = {
            "workflow": "customer_support",
            "choice": step["choice"],
            "model": selected["name"],
            "route_mean_ms": route_ms,
            "route_samples_ms": times,
            "target_result": target,
            "target_call": call,
            "routing_samples": samples,
        }
    for run in raw["runs"]:
        sides = run["cases"]["support"]["sides"]
        for stage in (0, 1):
            inputs = [sides[p]["steps"][stage]["model_input"] for p in PROVIDERS]
            assert all(x == inputs[0] for x in inputs)
    return comparison


def main():
    (OUT / "assets").mkdir(parents=True, exist_ok=True)
    (OUT / "sources").mkdir(exist_ok=True)
    V.OUT, V.ASSETS, V.SOURCES = OUT, OUT / "assets", OUT / "sources"
    V.EVIDENCE.clear()
    V.IMAGES.clear()
    V.SLIDES.clear()
    original_hash = V.digest(BASE)
    old = V.load(V.record(ROOT / "docs/presentations/v14/build-manifest.json"))
    raw = V.load(V.record(DATA / "results.json"))
    summary = V.load(V.record(DATA / "summary.json"))
    diagnostic = V.load(V.record(DATA / "candidate-order-diagnostic.json"))
    comparison = support_evidence(raw, summary)
    save(OUT / "sources/support-original-comparison.json", comparison)
    V.record(OUT / "sources/support-original-comparison.json")
    calls = [c for c in diagnostic["calls"] if c["variant"] == "original"]
    assert len(calls) == 3
    first_request = calls[0]["request"]
    for call in calls:
        request = call["request"]
        assert request["state"] == first_request["state"]
        assert request["questions"] == first_request["questions"]
        assert call["status"] == 200 and call["response"]["source"] == "live"
        assert call["response"]["answers"]["action"]["choice"] == call["candidate_order"][0]
    save(OUT / "sources/order-original-diagnostic.json", {
        "classification": "internal only",
        "source": str((DATA / "candidate-order-diagnostic.json").relative_to(ROOT)),
        "calls": calls,
    })
    V.record(OUT / "sources/order-original-diagnostic.json")

    prs = Presentation(BASE)
    originals = list(prs.slides._sldIdLst)
    metas = []
    for index, meta in enumerate(old["slides"], 1):
        metas.append({**meta, "source": "v14", "v14_page": index, "body_modified": False})

    notes = (
        "新增支付客服原始流程复测页；不修改既有优化流程，也不引入增强输入结果。"
        "数据来自2026-10-16已保存的真实调用，没有为制图重新调用模型。"
        "原始流程：模型读取纯任务选择customer_support，再读取同一候选证据选择型号；"
        "程序执行所选型号，没有程序评分替换。三路同阶段model_input一致。"
        "路由耗时为该任务三轮、每轮两次判断的后端耗时均值，预热不计入；"
        "目标答复为每路一次独立调用。路由均值不能与单次目标耗时相加当成单次端到端测量。"
        "GPT路由器为GPT-6 Sol low；所选目标按当前评测配置使用max档位，"
        "输出预算4096 token、SDK响应等待40秒，不重试、不换型号。"
        "Decider路径目标HTTP504，未返回最终答复。"
        "Jev路径目标Opus5.5此次完成，原文加入未提供的30分钟处理时限，完整文本不等于业务正确。"
        "GPT路径目标Luna此次完成，答复建议等待确认、不要重复付款；"
        "其系统已检测到转账等陈述仍需业务核验，没有进行全面客服质量评分。"
        "前一页保留2026-10-15单次历史观察，本页展示新的原始流程复测，未改写旧数据。"
        "\n完整原始请求、路由响应和目标返回：sources/support-original-comparison.json\n"
        + json.dumps(
            {p: {k: v for k, v in row.items() if k != "routing_samples"}
             for p, row in comparison["routes"].items()},
            ensure_ascii=False, indent=2,
        )
    )
    s = V.base(
        prs, "04  模型路由 · 支付客服原始流程",
        "支付客服复测：选型不变，实际答复与等待时间不同",
        "2026-10-16 · 原始流程：模型选客服流程 → 模型选型号 → 目标模型生成答复。",
        "", notes,
    )
    support_node = prs.slides._sldIdLst[-1]
    support_meta = {**V.SLIDES[-1], "source": "new", "body_modified": True}
    V.icon(s, "headset", .84, 2.28, L.TEAL, .35)
    L.rect(s, 1.36, 2.15, 11.30, .86, L.PALE_TEAL, radius=True)
    L.text(s, comparison["task"], 1.55, 2.30, 10.88, .57, 17)
    L.text(s, "三路流程输出均为 customer_support；路由均值取 3 轮，目标答复每路实测 1 次。",
           .72, 3.14, 11.96, .31, 13.5, L.MUTED)
    quotes = {
        "jev": '但订单状态在 30 分钟后仍未更新',
        "bedrock": '请耐心等待确认完成，期间无需重复转账。',
    }
    for i, (provider, label, color) in enumerate(zip(
        PROVIDERS, ("Strands Decider", "Jev", "GPT-6 Sol · low"),
        (L.TEAL, V.PURPLE, L.BLUE), strict=True,
    )):
        row = comparison["routes"][provider]
        target = row["target_result"]
        x = .68 + i * 4.07
        V.card(s, x, 3.66, 3.84, 2.44, label, "", color)
        L.text(s, f'路由 {row["route_mean_ms"]:.1f} ms', x + .18, 4.16, 3.48, .32, 16, color, True)
        L.text(s, f'{row["choice"]} → {row["model"].replace("Claude ", "")}',
               x + .18, 4.61, 3.48, .36, 16)
        if provider == "decider":
            status = "目标 HTTP 504"
            detail = "40 秒响应等待上限触发\n未获得最终答复"
        else:
            status = f'目标 {target["backend_total_ms"] / 1000:.2f} s · 返回答复'
            quote = quotes[provider]
            assert quote in target["text"].replace("**", "")
            detail = f"“{quote}”"
        L.text(s, status, x + .18, 5.05, 3.48, .32, 15, color, True)
        L.text(s, detail, x + .18, 5.49, 3.48, .56, 12.5)
    V.callout(
        s, "Decider 路由最快；GPT 路径本次更早得到答复。Jev 答复中的“30 分钟”缺少输入依据。",
        y=6.18, size=13.7,
    )

    notes = (
        "internal only。本页仅展示原始输入的候选顺序诊断，现有优化流程页面保持原样。"
        "原始输入使用MIRACL dev en/zh平均分：English30.651，Multilingual59.225，v4 50.6635；"
        "这些分数与型号、候选ID、问题说明、业务任务完全不变，只改变criteria对象的插入顺序。"
        "原始请求427输入token，远小于4096上下文上限；直接调用/api/decide，mode=live。"
        "三次分别返回m00、m02、m01，均为当前排列首项。模型概率由服务真实返回，"
        "没有程序改写答案、把候选排名用于选型，或在此页计为准确率。"
        "独立诊断共保存六次调用，本页只取其中三次原始输入，不混入正式路由耗时。"
        "已经确认当前调用链在本样例对顺序敏感；尚未隔离模型、提示表达及底层推理实现各自影响。"
        "不能推广为全部Decider任务偏好首项，也未对Jev/GPT做同等顺序诊断。"
        "后续：核对渲染与候选读回；在不同任务和全部候选排列下重测；"
        "再验证模型分步判断或用随机排列训练数据适配。始终保留模型真实答案。"
        "\n原始请求与响应：sources/order-original-diagnostic.json\n"
        + json.dumps(calls, ensure_ascii=False, indent=2)
    )
    s = V.base(
        prs, "内部诊断 · 候选顺序",
        "只交换选项顺序，Decider 的选择随之改变",
        "中英文工单索引：任务、候选 ID、评测数值与问题说明不变；仅改变候选排列。",
        "", notes,
    )
    order_node = prs.slides._sldIdLst[-1]
    order_meta = {**V.SLIDES[-1], "source": "new", "body_modified": True,
                  "classification": "internal only"}
    L.rect(s, 10.66, .30, 2.0, .44, L.PALE_ORANGE, radius=True)
    L.text(s, "internal only", 10.90, .36, 1.61, .28, 13, L.ORANGE, True)
    L.rect(s, .68, 2.14, 11.98, .76, L.PALE_BLUE, radius=True)
    L.text(s, "业务任务：将中英文支付工单转换为知识库检索向量。", .88, 2.24, 11.55, .29, 17)
    L.text(s, "候选证据 · 中英文检索均分：English 30.651 / Multilingual 59.225 / v4 50.6635",
           .88, 2.60, 11.55, .23, 12.2, L.MUTED)
    name_map = {"m00": "English", "m01": "Multilingual", "m02": "v4"}
    rows = []
    for call in calls:
        choice = call["response"]["answers"]["action"]["choice"]
        rows.append([
            " → ".join(name_map[key] for key in call["candidate_order"]),
            f"{choice} · {name_map[choice]}",
            "第 1 项",
        ])
    V.table(s, ["候选排列（原始输入）", "Decider 实际返回", "选中位置"],
            rows, [6.13, 3.68, 2.17], y=3.16, row_h=.56, size=16)
    V.card(s, .68, 5.57, 5.85, .80, "已观察：3 次都选择首项",
           "本样例存在顺序敏感性，未形成稳定的证据比较。", L.ORANGE, size=12.4)
    V.card(s, 6.72, 5.57, 5.94, .80, "待定位：模型行为与推理实现",
           "下一步核对候选映射，并扩大任务与排列测试。", L.TEAL, size=12.4)

    ordered = originals[:18] + [support_node] + originals[18:] + [order_node]
    metas = metas[:18] + [support_meta] + metas[18:] + [order_meta]
    for node in list(prs.slides._sldIdLst):
        prs.slides._sldIdLst.remove(node)
    for node in ordered:
        prs.slides._sldIdLst.append(node)
    assert len(prs.slides) == 32
    for index, (slide, meta) in enumerate(zip(prs.slides, metas, strict=True), 1):
        for shape in list(slide.shapes):
            if shape.has_text_frame and Inches(6.5) <= shape.top < Inches(7.03):
                assert not shape.text.strip()
                shape._element.getparent().remove(shape._element)
            elif shape.has_text_frame and shape.top > Inches(7) and re.fullmatch(
                r"\d+ / \d+", shape.text
            ):
                replace_text(shape, shape.text, f"{index:02} / 32")
        meta.update(page=index, notes=slide.notes_slide.notes_text_frame.text)
    prs.core_properties.title = "Strands Decider × Jev × GPT：原始客服路由与选项顺序诊断"
    prs.core_properties.subject = "v15 / 32 pages / final slide: internal only"
    path = OUT / f"{NAME}.pptx"
    prs.save(path)
    assert V.digest(BASE) == original_hash
    save(OUT / "build-manifest.json", {
        "created_at": datetime.now(UTC).isoformat(),
        "base_file": str(BASE.relative_to(ROOT)), "base_sha256": original_hash,
        "pptx_file": str(path.relative_to(ROOT)), "pptx_sha256": V.digest(path),
        "pages": 32, "new_pages": [19, 32], "slides": metas,
        "evidence": V.EVIDENCE, "images": V.IMAGES,
        "preserved_original_pages": 30, "optimization_pages_unchanged": True,
        "internal_only_pages": [32], "new_model_calls": 0,
        "aws_resource_mutations": 0, "lower_explanatory_rows": 0,
    })
    rows = ["# v15 讲稿与来源", "",
            "新增 P19 原始客服路由复测、P32 候选顺序诊断（internal only）。既有 30 页正文与图片保留。", ""]
    for meta in metas:
        rows += [f'## {meta["page"]:02} · {meta["title"]}', "", meta["notes"], ""]
    (OUT / "讲稿与来源.md").write_text("\n".join(rows))
    print(json.dumps({"pptx": str(path), "pages": 32, "new_pages": [19, 32]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
