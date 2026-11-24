"""Reorganize v19 around decision models, two demos, input optimization and SFT."""

import json
import re
import shutil
from datetime import UTC, datetime

import build_presentation_v10 as V
import presentation_layout as L
from build_presentation_v5 import replace_text
from build_presentation_v19 import REPO as FT_REPO
from build_presentation_v19 import WALK, replace_in, rounded
from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt

ROOT = V.ROOT
OUT = ROOT / "docs/presentations/v20"
BASE = next((ROOT / "docs/presentations/v19").glob("*.pptx"))
NAME = "Strands-Decider-v20-决策模型趋势与业务优化实践-40页-20261021"
PAGES = 40
BOARD = "https://benchmarkheaven.com/jev-models/api"
STRANDS = "https://strandsagents.com/blog/introducing-strands-decider/"
JEV = "https://typesafe.ai/blog/introducing-system-one-models-and-jev"
PROVIDERS = ("decider", "jev", "bedrock")
LABELS = ("Decider", "Jev", "GPT")
CASE_LABELS = {"support": "高频客服", "code": "交易代码", "analysis": "结算分析", "retrieval": "中英文索引"}
MODEL_NAMES = {
    "anthropic.claude-opus-5": "Opus 5",
    "anthropic.claude-opus-5-5": "Opus 5.5",
    "anthropic.claude-sonnet-5-5": "Sonnet 5.5",
    "anthropic.claude-haiku-5-5": "Haiku 5.5",
    "openai.gpt-6-luna": "GPT-6 Luna",
    "openai.gpt-6.1-sol": "GPT-6.1 Sol",
    "cohere.embed-english-v3": "Embed English",
    "cohere.embed-multilingual-v3": "Embed Multilingual",
}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def compact_multiline(slide):
    """Keep both lines inside newly drawn table cells."""
    for shape in slide.shapes:
        if shape.has_text_frame and "\n" in shape.text:
            for paragraph in shape.text_frame.paragraphs:
                paragraph.space_after = Pt(1)
                paragraph.line_spacing = 1.05


def main():
    for part in ("assets", "sources/notes-v19", "screenshots"):
        (OUT / part).mkdir(parents=True, exist_ok=True)
    V.OUT, V.ASSETS, V.SOURCES = OUT, OUT / "assets", OUT / "sources"
    V.EVIDENCE.clear()
    V.IMAGES.clear()
    V.SLIDES.clear()
    base_hash = V.digest(BASE)
    previous = V.load(BASE.parent / "build-manifest.json")
    snapshots = []
    for relative in [
        "docs/results/model-evidence-2026-10-16/summary.json",
        "docs/results/model-evidence-2026-10-16/results.json",
        "docs/MODEL_ROUTING_OPTIMIZATION.md",
        "docs/presentations/v12/sources/payment-comparison.json",
        "docs/presentations/v12/sources/payment-live.json",
        "docs/presentations/v16/sources/support-routing-original.json",
        "docs/presentations/v17/sources/summary.json",
        "docs/presentations/v19/sources/reports/2026-10-16/summary.json",
        "docs/presentations/v19/sources/reports/2026-10-16/training.json",
        "docs/presentations/v19/sources/reports/2026-10-16/replay-verification.json",
        "docs/presentations/v19/sources/docs/diagrams/tetris-sft-workflow.svg",
        "docs/presentations/v19/sources/docs/diagrams/tetris-sft-workflow.png",
        "docs/presentations/v19/sources/docs/diagrams/lora-training-step.svg",
        "docs/presentations/v19/sources/docs/diagrams/lora-training-step.png",
        "docs/presentations/v11/sources/cost-calculation.json",
        "docs/presentations/v9/sources/vllm/jev-summary.json",
        "docs/presentations/v9/sources/vllm/original-benchmark.json",
        "docs/presentations/v9/sources/vllm/vllm-recheck.json",
    ]:
        source = ROOT / relative
        target = OUT / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        V.record(target)
        snapshots.append({"source": relative, "snapshot": str(target.relative_to(ROOT)),
                          "sha256": V.digest(target)})
    for p in (OUT / "sources/web").iterdir():
        if p.is_file():
            V.record(p)
    rows = V.load(OUT / "sources/web/jevbench-rows.json")
    jev = next(r for r in rows if r["key"] == "jev-1.13.0")
    assert jev["rank"] == 9
    assert sum(row["ranked"] is True for row in rows) == 39
    route = V.data("docs/results/model-evidence-2026-10-16/summary.json")
    requests = V.data("docs/results/model-evidence-2026-10-16/results.json")
    assert len(requests["runs"]) == 3
    sample = requests["runs"][0]["cases"]["retrieval"]
    original_input = sample["sides"]["decider"]["steps"][1]["model_input"]
    optimized_input = sample["optimizedSides"]["decider"]["steps"][1]["model_input"]
    for run in requests["runs"]:
        for case in run["cases"].values():
            for key in ("sides", "optimizedSides"):
                for step in (0, 1):
                    model_inputs = [case[key][p]["steps"][step]["model_input"] for p in PROVIDERS]
                    assert all(value == model_inputs[0] for value in model_inputs)
            for provider in PROVIDERS:
                a = case["sides"][provider]["steps"]
                b = case["optimizedSides"][provider]["steps"]
                assert a[0] == {k: v for k, v in b[0].items() if k != "shared_classification"}
                assert b[0]["shared_classification"] is True
                assert list(a[1]["model_input"]["questions"]["action"]["criteria"]) == list(
                    b[1]["model_input"]["questions"]["action"]["criteria"]
                )
    V.record(OUT / "screenshots/jevbench-leaderboard.png")
    crop = (165, 205, 2260, 500)
    with Image.open(OUT / "screenshots/jevbench-leaderboard.png") as image:
        image.crop(crop).save(OUT / "screenshots/jevbench-ranking-detail.png")
    V.record(OUT / "screenshots/jevbench-ranking-detail.png")
    prs = Presentation(BASE)
    old_ids = list(prs.slides._sldIdLst)
    order, metas = [], []

    def keep(page, section, edits=()):
        s = prs.slides[page - 1]
        order.append(old_ids[page - 1])
        meta = {**previous["slides"][page - 1], "source": "v19", "v19_page": page,
                "content_modified": bool(edits), "section": section}
        for before, after in edits:
            if "\n" in before:
                assert before.count("\n") == after.count("\n")
                for old_line, new_line in zip(before.split("\n"), after.split("\n"), strict=True):
                    replace_in(s, old_line, new_line)
            else:
                replace_in(s, before, after)
            assert any(shape.has_text_frame and after in shape.text for shape in s.shapes), after
            if meta["title"] == before:
                meta["title"] = after
        for shape in s.shapes:
            if shape.has_text_frame and Inches(.30) <= shape.top <= Inches(.36) and shape.text:
                replace_text(shape, shape.text, section)
        metas.append(meta)
        return s

    def new(section, title, subtitle, notes):
        s = V.base(prs, section, title, subtitle, "", notes)
        order.append(prs.slides._sldIdLst[-1])
        metas.append({**V.SLIDES[-1], "source": "new", "content_modified": True})
        return s

    # 1–7: The product trend, typed decisions, use cases, model classes and external evaluation.
    keep(1, "01  决策模型 · 从趋势到落地", [
        ("让决策模型接得住真实业务", "让决策模型接得住真实业务"),
        ("Strands Decider、Jev 与 GPT：原理、业务验证、领域微调、vLLM 部署与成本",
         "市场趋势与模型原理 → 两类业务实测 → 输入优化与微调 → vLLM 部署与成本"),
        ("业务事实 → 候选动作\n核对证据和完成条件", "支付客服与模型路由\n用实测验证选择与效果"),
        ("单次延迟 → 并发吞吐\n看完整请求和尾延迟", "上下文与判断条件 → 微调\n改善领域决策，再部署 vLLM"),
    ])
    s = new(
        "01  市场趋势",
        "决策模型正在形成独立产品：开源权重、托管 API 与专用评测",
        "围绕软件中的高频选择，模型开始以候选 ID、真值和评分作为主要输出。",
        "这是依据公开发布和评测产品形态作出的趋势观察，不是市场规模、收入或份额统计。"
        "TypeSafe 的 Jev 发布文日期 2026-09-15，提出 System One 模型及 RLCD，提供托管 API。"
        "Strands 官方发布文日期 2026-10-01，公布 2B 开源模型、权重、训练数据和脚本，"
        "强调低延迟实验和混合智能体。Jev 的内部架构不能据此推断为 Decider 的 pointer head。"
        "2026-10-21 JevBench API 页面显示 39 个上榜系统；这只是该榜单口径，"
        "不代表市场只有 39 个产品，也不等于本项目评测过这些产品。"
        "厂商宣传倍数未作为本项目实测结论。\n"
        f"Strands：{STRANDS}\nTypeSafe：{JEV}\n榜单：{BOARD}",
    )
    for i, (title, body, glyph, color) in enumerate([
        ("开源与自托管", "Strands Decider 2B\n权重、数据与训练代码开放\n可做领域适配与私有部署", "layers", L.TEAL),
        ("托管决策 API", "TypeSafe Jev / System One\n提交事实、问题与候选\n按调用用量消费模型能力", "workflow", V.PURPLE),
        ("专用评测生态", "JevBench 托管 API 榜单\n39 个上榜系统\n质量、概率、速度与成本并看", "chart-no-axes-combined", L.BLUE),
    ]):
        V.panel(s, title, body, .74 + i * 4.04, y=2.30, w=3.82, h=3.62,
                color=color, icon_name=glyph, size=16.5)
    V.hyperlink(s, "Strands 发布说明 ↗", STRANDS, .80, 6.14, 3.8, 13.5)
    V.hyperlink(s, "Jev / System One 发布说明 ↗", JEV, 4.85, 6.14, 4.2, 13.5)
    V.hyperlink(s, "JevBench 榜单 ↗", BOARD, 9.03, 6.14, 3.5, 13.5)
    keep(2, "01  决策模型的基本工作方式")
    s = new(
        "01  适用场景",
        "适合高频、有限选项的判断；每项决策都要有可核对的事实",
        "模型选择动作或类别，业务系统执行；开放式回复、代码与分析交给生成模型。",
        "复用 v9 适用场景材料，结合本项目主线重排。以下是适用方向，"
        "不是对全部场景达到生产质量的声明。客服选择指队列、工具、跟进动作或回复策略，"
        "不是用 Decider 直接生成任意客服文本。模型路由需要真实候选能力资料和下游任务验收。"
        "金融或权限硬条件仍由执行系统检查；这类保护不能记作模型选择正确。"
        "共享上下文的一次请求支持多个独立判断，存在执行依赖的后续判断要另发请求。\n"
        f"依据：{STRANDS}\n场景来源：docs/presentations/v9/build-manifest.json",
    )
    cells = [
        ("客服与运营", "工单分流、升级队列、跟进等级", "headset"),
        ("模型与工具路由", "先选处理流程，再选型号或工具", "route"),
        ("支付与风控", "等待确认、补款、核验退款地址", "shield-check"),
        ("证据与质量检查", "依据是否充分、是否继续检索", "search"),
        ("内容与策略分类", "有限类别、风险等级、人工复核", "scan-eye"),
        ("游戏与局部控制", "读状态，在合法动作中作选择", "workflow"),
    ]
    for index, (title, body, glyph) in enumerate(cells):
        x, y = .72 + index % 3 * 4.07, 2.23 + index // 3 * 2.08
        L.rect(s, x, y, 3.86, 1.81, L.WHITE, L.LINE, True)
        V.icon(s, glyph, x + .20, y + .17, L.TEAL, .35)
        L.text(s, title, x + .70, y + .17, 2.94, .40, 19, L.TEAL, True)
        L.text(s, body, x + .20, y + .91, 3.43, .61, 16)
    keep(4, "01  BERT / 决策模型 / GPT")
    s = new(
        "01  JevBench · 公开榜单截图",
        "JevBench：综合分、能力分与业务准确率是不同的指标",
        "托管 API 榜单节选：d1、Mercury Decide 与 Jev 1.13.0；完整榜单可点击下方链接。",
        "2026-10-21 真实浏览器截图，裁剪第 7–9 名，不修改页面数据。"
        "完整原图和截图时间、SHA-256 保存在 sources/web/screenshot.json 与 screenshots。"
        "Jev1.13 综合分 71.491186，Intelligence=63.620758，Calibration=90.632202，"
        "能力分=(I+C)/2=77.12648；63.6 是 Intelligence 轴，不是能力分。"
        "综合分还考虑速度与成本，网站公式带低分轴惩罚，不能读成正确率。"
        "中位延迟为 API 榜单口径，与本项目 ECS/Lambda 后端计时不同。"
        "本页仅引用第三方公开榜单；下一页 Decider 231 题是项目自测，不能并入正式排名。\n"
        f"来源：{BOARD}\n公式：https://benchmarkheaven.com/jev-models/methodology",
    )
    L.picture(s, OUT / "screenshots/jevbench-ranking-detail.png", .70, 2.22, 11.95, 1.95, True)
    values = [
        ("Jev 综合分", f'{jev["jevbench_score"]:.1f}', "同时计入质量、速度与成本", L.TEAL),
        ("Jev 能力分", f'{(jev["axes"]["intelligence"] + jev["axes"]["calibration"]) / 2:.1f}',
         "Intelligence 与 Calibration 的均值", V.PURPLE),
        ("Jev API 中位延迟", f'{jev["speed"]["p50_s_adjusted"]:.2f} s', "第三方实测口径", L.BLUE),
    ]
    for i, (label, value, caption, color) in enumerate(values):
        x = .72 + i * 4.06
        L.rect(s, x, 4.49, 3.86, 1.36, L.WHITE, L.LINE, True)
        L.text(s, label, x + .16, 4.60, 3.54, .31, 16, color, True)
        L.text(s, value, x + .16, 5.00, 3.54, .44, 28, color, True)
        L.text(s, caption, x + .16, 5.55, 3.54, .23, 11.5, L.MUTED)
    V.hyperlink(s, "完整评测、分项与方法：benchmarkheaven.com/jev-models/api ↗",
                BOARD, .77, 6.17, 11.80, 14)
    keep(7, "01  JevBench · 本项目 Decider 实测", [
        ("原版 v19 · g6.xlarge / L4 · 单并发 231 题 · 私网客户端计时",
         "本项目原版 v19 · 231 道公开题 · 单并发私网计时；尚无完整榜单总分"),
    ])

    # 8–17: Specific mechanism and two demo modules before discussing remedies.
    keep(3, "02  Strands Decider · 实现原理")
    s = keep(9, "02  AWS 演示架构", [
        ("EC2 g6.xlarge\nDecider v19", "EC2 g6.xlarge\nDecider / vLLM"),
        ("VPC · us-east-1", "业务 VPC · us-east-1"),
    ])
    # Retain all AWS icons and service positions while correcting the shared GPU VPC boundary.
    s.shapes[9].height = Inches(2.02)
    s.shapes[11].height = Inches(1.52)
    L.rect(s, 3.43, 4.92, 4.98, 1.27, None, V.PURPLE)
    L.text(s, "共享 GPU VPC\n私网互联", 3.62, 5.12, 1.90, .74, 12, V.PURPLE, True)
    s.notes_slide.notes_text_frame.text = (
        "当前演示前端为私有 S3 + CloudFront OAC，后端 ECS Fargate；用户名密码登录，未使用 Cognito。"
        "ECS 直接经私网访问共享 g6.xlarge vLLM Decider 服务；Bedrock 负责 GPT，"
        "Jev 保留应用 Lambda 适配。图省略网络端点节点。"
        "当前调用链依据 deployment/DEPLOYED.md；本次制作不变更部署。"
        "后续支付和路由数字复用此前原版 Decider 的真实采集，未改写成 vLLM 性能；"
        "vLLM 独立对照在第 32–34 页，具体批次、GPU与计时边界在各页备注中保留。"
    )
    for page in (10, 11, 12):
        keep(page, "03  演示一 · 支付客服固定流程")
    keep(18, "04  演示二 · 模型路由")
    keep(21, "04  模型路由 · 客服原始流程")
    s = new(
        "04  模型路由 · 四任务实测",
        "流程分流都正确，具体型号仍有明显差异",
        "四个任务各重复三轮；每格为原始流程的所选型号与两次决策平均耗时。",
        "数据统一来自 2026-10-16 model-evidence-v1 保存批次的 original 路径；"
        "四任务×三轮，每路由器 12 个样本，所有流程选择与预设任务类型一致。"
        "每轮两步都由对应模型判断；三侧任务、候选ID、顺序和证据相同；"
        "这里的耗时是 workflow + model 的 backend_total_ms 均值，不含目标执行。"
        "不是把与 GPT 型号一致计为准确率；文本型号存在质量/成本/响应的取舍。"
        "中英文任务选择 Embed English 与给定检索证据不符。"
        "下一页历史界面截图保留同一任务和同样选择，但不拿截图单次耗时当本页均值。"
        "\n来源：docs/results/model-evidence-2026-10-16/summary.json、results.json",
    )
    table = []
    for key, label in CASE_LABELS.items():
        row = [label]
        for provider in PROVIDERS:
            record = route["cases"][key][provider]
            assert len(record["original_choices"]) == 1
            row.append(f'{MODEL_NAMES[next(iter(record["original_choices"]))]}\n'
                       f'{record["original_route"]["mean_ms"]:.1f} ms')
        table.append(row)
    V.table(s, ["任务", *LABELS], table, [2.05, 3.33, 3.33, 3.29], y=2.24, row_h=.73, size=16)
    compact_multiline(s)
    V.callout(s, "Decider 本批路由最快；双语索引误选英文模型，说明流程正确仍需继续验证型号选择。",
              y=6.12, size=14)
    keep(20, "04  模型路由 · 实际输入与截图")
    keep(22, "05  其他决策场景 · 最终结果")

    # 18–25: Detailed input optimization with both a successful and an unresolved example.
    s = new(
        "06  提高判断效果 · 先处理输入",
        "两条优化路线：先让条件更清楚，再让模型学会领域判断",
        "输入优化改变模型读到的信息；微调改变 LoRA 和决策头参数。两者分别验收。",
        "支付输入优化和模型路由证据优化均为已完成实验，未改权重。"
        "支付对照为同一批四场景20节点，Decider 14/20→18/20。"
        "路由增强证据仍未解决 Decider 中英文模型误选，不宣称准确率提升。"
        "后续 Tetris SFT 为另一个已完成的领域训练实验，不能当作支付或路由已完成微调。"
        "共同评估原则：保留模型原始choice、独立参考或业务验收、失败和候选换序检查；"
        "程序的执行保护不进入模型正确率。\n"
        "支付：docs/presentations/v12/README.md\n路由：docs/MODEL_ROUTING_OPTIMIZATION.md\n"
        f"微调：{WALK}",
    )
    V.panel(s, "路线一 · 输入优化",
            "只保留任务相关事实\n把适用范围写进每个候选\n说明指标含义与缺失信息\n\n同任务、同候选做前后对比",
            .74, y=2.27, w=5.83, h=3.99, icon_name="list-filter", size=17)
    V.panel(s, "路线二 · 领域微调",
            "收集真实错误与可靠标签\n训练 / 验证 / 校准 / 测试隔离\n续训 LoRA 与 Pointer Head\n\n测准确率，也测换序与任务结果",
            6.79, y=2.27, w=5.83, h=3.99, color=L.BLUE, icon_name="brain-circuit", size=17)
    for page in (13, 14, 15, 16):
        keep(page, "06  输入优化 · 支付客服")
    s = new(
        "06  输入优化 · 模型路由",
        "路由优化：把总分拆成任务相关证据，选择仍由模型完成",
        "用户只给任务；两路保留相同候选和顺序，优化能力事实与判断条件的表达。",
        "已完成的 model-evidence-v1 实验。原始流程保留紧凑 metrics JSON；"
        "增强路径改为分项自然语言描述，解释同指标高分更好、缺失为未知、"
        "参考价格不是 Bedrock 报价，测试推理配置不能外推。"
        "程序只提取、换算和格式化证据，不排名、不设winner、不预填答案。"
        "配对比较复用同一次workflow判断，再由该路由器分别完成原始/增强的型号判断；"
        "两条路径的两步耗时分别计算，同一provider实际共调用三次。"
        "9次预热另计，正式108次模型调用，程序选型0次。"
        "当前前端只展示原始路由，这些优化数字来自保存实验。\n"
        "来源：docs/MODEL_ROUTING_OPTIMIZATION.md、app/routing_evidence.py",
    )
    V.table(s, ["任务", "原始表达", "增强证据与判断条件"], [
        ["事实型客服", "综合质量 / 参考价格", "拆出 Language、IF；说明指令遵循与成本"],
        ["交易代码", "Coding 分数", "分开 Coding、Agentic Coding；业务安全另验收"],
        ["结算分析", "综合质量分数", "拆出 Reasoning、Mathematics、Data Analysis"],
        ["中英文索引", "en / zh 检索均分", "逐语言 nDCG@10；均分可能掩盖中文短板"],
    ], [1.63, 3.25, 7.12], y=2.21, row_h=.58, size=15.5)
    rounded(s, .73, 5.45, 5.80, 1.00, "不把答案写进输入",
            "不加品牌偏好、推荐 Top-1 或程序代选。", size=14)
    rounded(s, 6.79, 5.45, 5.81, 1.00, "看原始选择，再检查任务结果",
            "型号变化不等于准确率提高。", L.BLUE, size=14)
    s = new(
        "06  输入优化 · 实际请求",
        "把“检索均分 30.65”展开：英文 57.12，中文只有 4.18",
        "同一个中英文工单索引任务、同一个候选 m00；更精确地说明分数适用于哪种语言。",
        "本页请求节选直接取 results.json 第一轮 retrieval，原始与增强均保留同样三个ID、同样顺序。"
        "原始 m00 criteria 为紧凑 JSON，retrieval=30.651；增强原文为：\n"
        + optimized_input["questions"]["action"]["criteria"]["m00"]
        + "\n增强state里的task_evidence_guidance要求从任务判断语言、逐语言比较，"
        "指出英文分数不能证明中文能力；不指定winner。"
        "完整增强候选：Multilingual 英文57.802/中文60.648；v4 英文46.532/中文54.795。"
        "本次模型实际仍由各自推理做选择；下一页展示结果，不把清楚的提示当成已验证的改进。\n"
        "来源：sources/docs/results/model-evidence-2026-10-16/results.json",
    )
    task = json.loads(original_input["state"])["request"]
    L.rect(s, .72, 2.19, 11.88, .81, L.PALE_TEAL, radius=True)
    L.text(s, task, .89, 2.33, 11.50, .55, 16)
    rounded(s, .72, 3.27, 5.80, 3.04, "原始候选 · 紧凑指标",
            '"m00": {\n  "model": "Embed English",\n  "metrics": {"retrieval": 30.651}\n}\n\nen / zh 平均分，未逐语言展开。', size=15)
    rounded(s, 6.80, 3.27, 5.80, 3.04, "增强候选 · 实际请求节选",
            "Select Embed English to handle the requested\nembedding task. Measured English retrieval\n57.124/100; Chinese retrieval 4.178/100.\n\n判断条件：按任务所需语言分别比较，\n英文分数不能证明中文检索能力。", L.BLUE, size=14.5)
    s = new(
        "06  输入优化 · 路由对照结果",
        "更清楚的信息改变了选择，但本批未证明路由准确率提高",
        "每格为“原始 → 增强输入”的型号选择；四任务重复三轮，三个轮次的选择一致。",
        "2026-10-16四任务三轮配对实验；正式108次模型调用，没有程序选型。"
        "Decider三个文本场景均由Opus5变为Haiku5.5，双语索引仍选English。"
        "Jev/GPT客服由原型号变为GPT-6.1Sol；代码/结算/索引型号不变。"
        "每个路由器12对样本，底部均值取两次后端判断之和，不含目标执行。"
        "没有文本选型唯一正确标签，不能把与GPT一致或低价型号当作准确率。"
        "可明确判断的双语索引误选未改善，故不能声称增强证据提高了Decider选型准确率。"
        "此页保留路由结果，未重复展示客户目标执行的超时。后续优化转向任务标签、候选扰动和领域微调。"
        "\n原始对照：docs/results/model-evidence-2026-10-16/summary.json\n"
        "独立选项顺序问题保留在末页 internal only，不混入正式统计。",
    )
    choices = []
    for key, label in CASE_LABELS.items():
        row = [label]
        for provider in PROVIDERS:
            item = route["cases"][key][provider]
            old = MODEL_NAMES[next(iter(item["original_choices"]))]
            new_name = MODEL_NAMES[next(iter(item["optimized_choices"]))]
            row.append(f"{old}\n→ {new_name}")
        choices.append(row)
    V.table(s, ["任务", *LABELS], choices, [1.74, 3.40, 3.43, 3.43],
            y=2.22, row_h=.67, size=14.5)
    compact_multiline(s)
    for i, provider in enumerate(PROVIDERS):
        item = route["providers"][provider]
        x = .82 + 4.06 * i
        L.text(s, f'{LABELS[i]} 两步均值', x, 5.91, 3.75, .27, 13, L.MUTED)
        L.text(s, f'{item["original"]["mean_ms"]:.1f} → {item["optimized"]["mean_ms"]:.1f} ms',
               x, 6.23, 3.75, .28, 17, V.COLORS[i], True)

    # 26–31: Existing fine-tuning diagrams/results and a concrete transfer plan.
    for page in (24, 25, 26, 27, 28):
        edits = [("微调闭环：从可验证标签，到独立测试和完整对局",
                  "微调流程：从可验证标签，到独立测试和完整对局")] if page == 24 else []
        keep(page, "07  领域微调 · 俄罗斯方块实测", edits)
    s = new(
        "07  从微调实测到业务落地",
        "把同一训练方法用于支付与路由：先准备可靠标签，再验收",
        "俄罗斯方块已完成微调；以下是支付与模型路由的下一步方案，尚未得到新的业务准确率。",
        "这是一页明确标注的后续方案，不是已运行的新实验。"
        "支付可从现有错误与已审核业务规则构造选择题，覆盖退款/收款范围、金额、地址验证、"
        "缺证据、相似措辞与不同候选顺序；标签应由审核后的规则或人工确定。"
        "路由需要下游任务实测和质量/成本/延迟目标定义合格候选，不能把GPT选择或综合榜单第一直接当唯一标签。"
        "如果多个目标模型都合格，应构造明确比较目标的题或容许多个合格答案的评估；"
        "当前Tetris训练器是单标签choice，不声称已实现多标签训练。"
        "避免同模板变体和同一客户/任务跨训练测试分区。"
        "用官方渲染、候选换序与随机ID增强，续训LoRA+head，检查准确/误放行/下游完成与通用回归。"
        "Tetris的99.95%不可转写为支付或模型路由准确率。\n"
        f"可复用训练入口与说明：{FT_REPO}/blob/main/docs/FINETUNE.md",
    )
    rounded(s, .74, 2.22, 5.80, 2.44, "支付：让业务范围成为训练样本",
            "输入：收款 / 退款事实 + 同组候选\n标签：经审核的审批与处置结果\n困难题：未验证地址、少付、缺证据\n验收：误放行率、选择正确率与完成状态",
            size=16)
    rounded(s, 6.80, 2.22, 5.80, 2.44, "路由：用实际任务效果定义标签",
            "输入：任务 + 型号能力与调用限制\n标签：来自下游质量、延迟与成本测试\n困难题：双语短板、证据缺失、候选换序\n验收：下游任务通过率与成本 / 延迟",
            L.BLUE, size=16)
    V.flow(s, [
        ("隔离数据", "按任务来源与模板拆分", "layers"),
        ("续训参数", "LoRA + Pointer Head", "brain-circuit"),
        ("独立验收", "换序、误放行、业务结果", "clipboard-check"),
        ("保留回退", "新旧检查点并行比较", "workflow"),
    ], y=4.97, h=1.48)

    # 32–40: Short vLLM story, retained cost crossover plots, conclusions and internal appendix.
    keep(29, "08  vLLM · 部署与适配")
    keep(31, "08  vLLM · 原版性能对照")
    keep(33, "08  vLLM · 与 Jev 的并发对照")
    for page in (34, 35, 36):
        keep(page, "09  成本 · Jev / EC2 按需 / 一年 RI")
    keep(37, "10  总结 · 各自适用场景", [
        ("证据与完成判断需补强。", "输入优化与领域微调可继续改善。"),
    ])
    keep(38, "10  在线演示与项目资料")
    keep(39, "附录  internal only · 候选顺序诊断")
    used = {node.rId for node in order}
    for node in list(prs.slides._sldIdLst):
        prs.slides._sldIdLst.remove(node)
        if node.rId not in used:
            prs.part.drop_rel(node.rId)
    for node in order:
        prs.slides._sldIdLst.append(node)
    assert len(prs.slides) == PAGES, len(prs.slides)
    # Large raw JSON belongs in source files, not PowerPoint notes.
    shortened = []
    for index, (slide, meta) in enumerate(zip(prs.slides, metas, strict=True), 1):
        for shape in list(slide.shapes):
            if shape.has_text_frame and Inches(6.5) <= shape.top < Inches(7.03):
                assert not shape.text.strip(), (index, shape.text)
                shape._element.getparent().remove(shape._element)
            elif shape.has_text_frame and shape.top > Inches(7) and re.fullmatch(r"\d+ / \d+", shape.text):
                replace_text(shape, shape.text, f"{index:02} / {PAGES}")
        notes = slide.notes_slide.notes_text_frame.text
        if meta["source"] == "v19":
            page = meta["v19_page"]
            target = OUT / "sources/notes-v19" / f"page-{page:02}.txt"
            target.write_text(notes)
            V.record(target)
            if len(notes) > 2600:
                notes = notes.split("\n{", 1)[0].split("\n[", 1)[0]
                if len(notes) > 2400:
                    notes = notes[:2300].rsplit("。", 1)[0] + "。"
                notes += f"\n完整继承备注与原始引用：sources/notes-v19/page-{page:02}.txt。"
                slide.notes_slide.notes_text_frame.text = notes
                shortened.append(index)
        meta.update(page=index, notes=notes)
    prs.core_properties.title = "Strands Decider：决策模型趋势与业务优化实践"
    prs.core_properties.subject = "v20 / 40 pages / market, two demos, context optimization, SFT, vLLM, cost"
    prs.core_properties.modified = datetime.now(UTC)
    target = OUT / f"{NAME}.pptx"
    prs.save(target)
    assert V.digest(BASE) == base_hash
    old_pages = [meta["v19_page"] for meta in metas if meta["source"] == "v19"]
    save(OUT / "build-manifest.json", {
        "created_at": datetime.now(UTC).isoformat(), "pages": PAGES,
        "base_file": str(BASE.relative_to(ROOT)), "base_sha256": base_hash,
        "pptx_file": str(target.relative_to(ROOT)), "pptx_sha256": V.digest(target),
        "reused_v19_pages": old_pages, "reused_pages": len(old_pages),
        "new_pages": [m["page"] for m in metas if m["source"] == "new"],
        "removed_v19_pages": sorted(set(range(1, 40)) - set(old_pages)),
        "removed_customer_closed_loop": True, "internal_only_pages": [40],
        "shortened_notes_pages": shortened, "slides": metas, "evidence": V.EVIDENCE,
        "source_snapshots": snapshots,
        "board_screenshot": {
            "url": BOARD, "original": "screenshots/jevbench-leaderboard.png",
            "crop": list(crop), "rendered": "screenshots/jevbench-ranking-detail.png",
            "data_replaced": False,
        },
        "new_model_calls": 0, "new_training_jobs": 0, "lower_explanatory_rows": 0,
    })
    lines = ["# v20 讲稿与来源", "", "按趋势、原理、两类实测、输入优化、微调、vLLM 和成本重排。",
             "复用 v19 的 31 页，新建 9 页。客服闭环页删除；末页 internal only 保留。",
             "历史实验的 GPU、计时与模型版本保留在备注中；不把原版数字改称 vLLM 数据。",
             "所有实验沿用已保存结果；新版只新采集公开网页及榜单截图。", ""]
    for meta in metas:
        lines += [f'## {meta["page"]:02} · {meta["title"]}', "", meta["notes"], ""]
    (OUT / "讲稿与来源.md").write_text("\n".join(lines))
    (OUT / "README.md").write_text(
        "# v20 · 决策模型趋势与业务优化实践\n\n"
        f"[PPTX](./{NAME}.pptx) · [PDF](./{NAME}.pdf) · "
        "[全篇预览](./slides-overview.png) · [讲稿与来源](./讲稿与来源.md)\n\n"
        "**40 页，复用 v19 的 31 页，新建 9 页。** 按新的故事线重排，"
        "保留 BERT / 决策模型 / GPT 对比、真实业务截图、微调流程图、其他场景结果与成本交叉图；"
        "删除支付客服中的自主闭环页，压缩 vLLM 并发细节。\n\n"
        "| 页码 | 内容 |\n| --- | --- |\n"
        "| P1–7 | 市场趋势、决策方式、适用场景、三类模型、JevBench 新截图与 Decider 自测 |\n"
        "| P8–9 | Strands Decider 原理与当前 AWS / vLLM 演示架构 |\n"
        "| P10–12 | 支付客服：真实请求、三个模型逐步选择、耗时与结果 |\n"
        "| P13–16 | 模型路由：四类任务、原始客服两步决策、同批均值与索引截图 |\n"
        "| P17 | 其他场景的最终业务效果与性能 |\n"
        "| P18–25 | 输入优化：支付字段和条件、前后截图、剩余错误；路由分项证据、实际请求与前后结果 |\n"
        "| P26–31 | 微调：复用流程图、标签样本、参数更新、独立测试、完整对局；支付和路由后续方案 |\n"
        "| P32–34 | vLLM 适配部署、原版对照、与 Jev 的简要并发对比 |\n"
        "| P35–37 | Jev / EC2 On-Demand / 一年 RI 的计费与交叉图 |\n"
        "| P38–40 | 总结、演示和仓库链接、internal only 顺序诊断附录 |\n\n"
        "输入优化与微调分别讲效果：支付 Decider 参考一致 14/20 → 18/20；"
        "路由证据增强尚未证明选型准确率提升；Tetris 微调比较题准确率 60.35% → 99.95%，"
        "换序后实际落点一致率 35.44% → 99.85%。这些结果不可混合成通用准确率。\n\n"
        "JevBench 截图于 2026-10-21 采集，保留原图与裁剪坐标。"
        "Jev 1.13 综合分 71.5，能力分 77.1；63.6 是 Intelligence 单轴分。"
        "市场趋势来自公开产品发布与评测生态，不使用未经证实的市场规模。\n\n"
        "原 PPT 的大段 JSON 备注移到 [继承备注](./sources/notes-v19/)，"
        "正文不添加页脚上方的采集说明行。当前架构注明 vLLM；历史实测仍按当时部署解释。"
        "**P40 为 internal only，未推送 GitHub。**\n\n"
        "[构建与复用清单](./build-manifest.json) · [数据与渲染核验](./render-audit.json)。"
        "构建：`scripts/build_presentation_v20.py`；检查：`scripts/verify_presentation_v20.py`。\n"
    )
    print(json.dumps({"pptx": str(target), "pages": PAGES, "reused_pages": len(old_pages)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
