"""Extend v23 with decision-model positioning, customer cases and AgentCore.

Preserve retained slide bodies, embedded screenshots and source measurements.
Official claims, user-supplied customer results, measured infrastructure results
and planned integrations have separate provenance.
"""

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import cairosvg
import build_presentation_v10 as V
import presentation_layout as L
from build_presentation_v5 import replace_text
from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

ROOT = V.ROOT
OUT = ROOT / "docs/presentations/v24"
BASE = next((ROOT / "docs/presentations/v23").glob("*.pptx"))
AC = ROOT.parent / "StrandsDeciderAgentCore"
NAME = "Strands-Decider-v24-快速决策与AgentCore-GPU方案-41页-20261026"
PAGES = 41
JEV_URL = "https://typesafe.ai/blog/introducing-system-one-models-and-jev"
AC_DOC = "https://github.com/milan9527/StrandsDecider/blob/main/docs/AGENTCORE_GPU.md"
AWS = "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/"
NEW_PAGES = [8, 32, 33, 34, 35]
MODIFIED_PAGES = [1, 40]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def chapter(page):
    if page <= 7:
        return "01  快速决策 · 大模型的补充"
    if page == 8:
        return "02  适用场景 · 已实现与潜在应用"
    if page == 9:
        return "03  三模型演示架构"
    if page <= 16:
        return "04  支付客服 · 从误选到输入优化"
    if page <= 20:
        return "05  模型路由 · 看速度，也看选择"
    if page <= 22:
        return "06  图片决策 · 从截图到业务动作"
    if page == 23:
        return "07  其他场景 · 适用边界"
    if page <= 28:
        return "08  领域微调 · 从数据到结果"
    if page <= 31:
        return "09  vLLM · 部署与性能"
    if page <= 35:
        return "10  AgentCore · GPU Runtime 与 Agent 协作"
    if page <= 38:
        return "11  Jev 与 EC2 · 成本选择"
    return "12  总结与资料" if page < 41 else "附录  internal only · 候选顺序诊断"


def link(slide, label, url, x, y, w, size=12, color=L.TEAL):
    shape = L.text(slide, label, x, y, w, .32, size, color, True)
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            run.hyperlink.address = url
    return shape


def aws_icon(slide, name, x, y, w=.42):
    directory = OUT / "assets/official-icons"
    svg = directory / f"{name}.svg"
    png = directory / f"{name}.png"
    if not png.exists():
        cairosvg.svg2png(url=str(svg), write_to=str(png), output_width=256)
    V.record(svg)
    V.record(png)
    L.picture(slide, png, x, y, w, w)


def notes_remap(notes, old_page):
    if old_page == 8:
        notes = notes.replace("本版P29", "本版P30").replace("比较见P30", "比较见P31")
        notes = notes.replace("图片决策见P20–21", "图片决策见P21–22")
    if old_page == 34:
        notes = notes.replace("本版P22", "本版P23").replace("见P23–27", "见P24–28")
    return notes


def main():
    for folder in ("assets/official-icons", "sources/v23-notes", "screenshots"):
        (OUT / folder).mkdir(parents=True, exist_ok=True)
    V.OUT, V.ASSETS, V.SOURCES = OUT, OUT / "assets", OUT / "sources"
    V.EVIDENCE.clear()
    V.IMAGES.clear()
    V.SLIDES.clear()
    base_hash = V.digest(BASE)
    previous = V.load(BASE.parent / "build-manifest.json")
    for item in previous["evidence"]:
        source = ROOT / item["path"]
        assert V.digest(source) == item["sha256"], source
        if source.is_relative_to(BASE.parent):
            target = OUT / source.relative_to(BASE.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            V.record(target)
        else:
            V.record(source)
    for meta in previous["slides"]:
        target = OUT / "sources/v23-notes" / f'page-{meta["page"]:02}.txt'
        target.write_text(meta["notes"])
        V.record(target)

    for stem in ("jev-official", "aws-a2a", "aws-runtime-instances", "aws-evaluation"):
        for ext in ("html", "txt"):
            V.record(OUT / "sources" / f"{stem}.{ext}")
    official_text = (OUT / "sources/jev-official.txt").read_text()
    assert "70ms-500ms" in official_text and "40x-200x" in official_text
    claims = {
        "retrieved_at": datetime.now(UTC).isoformat(),
        "official_jev": {
            "url": JEV_URL,
            "quote": "End-to-end response time is 70ms-500ms for TypeSafe. "
            "This can range from 40x-200x faster for the same levels of frontier "
            "intelligence for System One shaped queries.",
            "attribution": "TypeSafe official launch article; vendor comparison, not this demo.",
        },
        "customer_case": {
            "source": "User supplied on 2026-10-26",
            "status": "已经实现",
            "customer": "自动驾驶客户",
            "task": "Jev 智能驾驶意图识别模型微调",
            "method": "RLCD 范式 DPO+LoRA",
            "choice_accuracy": .90,
            "latency_ms": 100,
            "sample_size": None,
            "latency_statistic_and_boundary": None,
            "independently_retested_in_this_project": False,
            "note": "Customer-case figures; not a claim about public Jev API fine-tuning support.",
        },
        "potential_case": {
            "customer": "智能家居客户",
            "task": "智能设备根据当前用户输入，自动选择动作和流程，都是固定选项",
            "status": "潜在场景",
            "measured_results": None,
        },
        "aws_sources": {
            "runtime_instances": AWS + "runtime-instances-how-it-works.html",
            "a2a": AWS + "runtime-a2a.html",
            "evaluation": AWS + "getting-started-on-demand.html",
        },
    }
    save(OUT / "sources/new-claims.json", claims)
    V.record(OUT / "sources/new-claims.json")
    for name in ("summary.json", "evaluations.json", "observability.json", "session-checks.json"):
        target = OUT / "sources" / f"agentcore-{name}"
        shutil.copy2(AC / "validation/agentcore_gpu" / name, target)
        V.record(target)
    for name in ("AGENTCORE_GPU.md",):
        target = OUT / "sources" / name
        shutil.copy2(ROOT / "docs" / name, target)
        V.record(target)
    for ext in ("svg", "png"):
        source = ROOT / "docs/diagrams" / f"agentcore-gpu-architecture.{ext}"
        target = OUT / "assets" / source.name
        shutil.copy2(source, target)
        V.record(target)
    for name in ("agentcore", "cloudwatch", "iam", "ec2", "s3", "ebs", "vpc", "cloud"):
        target = OUT / "assets/official-icons" / f"{name}.svg"
        shutil.copy2(AC / "docs/diagrams/aws-icons" / f"{name}.svg", target)
    summary = V.load(OUT / "sources/agentcore-summary.json")
    evaluations = V.load(OUT / "sources/agentcore-evaluations.json")
    assert summary["request_count"] == 24 and summary["business_sessions"] == 8
    assert len(evaluations) == 4 and all(
        result["value"] == 1.0 and result["label"] == "Correct"
        for row in evaluations for result in row["evaluation_results"]
    )

    prs = Presentation(BASE)
    original_ids = list(prs.slides._sldIdLst)
    generated = {}

    def new(key, page, title, subtitle, notes):
        slide = V.base(prs, chapter(page), title, subtitle, "", notes)
        for shape in list(slide.shapes):
            if shape.has_text_frame and Inches(6.5) <= shape.top < Inches(7.03):
                slide.shapes._spTree.remove(shape._element)
        generated[key] = {
            "id": prs.slides._sldIdLst[-1],
            "meta": {"source": "new", "v23_page": None, "title": title,
                     "content_modified": True, "notes": notes},
        }
        return slide

    # P1: explicit vendor attribution alongside the prominent speed numbers.
    cover_notes = (
        "本页以Jev官方速度数据说明决策模型的产品定位：它是大模型的补充，"
        "适合在有限候选中高频选动作、流程、工具或模型。复杂规划、代码和开放文本生成仍由大模型处理。"
        "\nTypeSafe官方发布文给出端到端70ms–500ms，并称在System One shaped queries上，"
        "相比其所比较的frontier models可快40x–200x。这里是厂商发布数据及厂商比较，"
        "不是本项目的Jev实测，不是所有任务或所有大模型的统一倍数。"
        "原文同时说明公开速度评测通常从美国西海岸笔记本发起；输入、地区、排队和模型版本会影响延迟。"
        "没有把首页另一个193.6x工作流宣传倍数混入本页。"
        "\n后续使用本项目支付、路由、图片、微调、vLLM和AgentCore数据逐项验证；"
        "不同批次、输入、调用路径和硬件不混算。"
        "\n来源：" + JEV_URL + "\n快照：sources/jev-official.txt；sources/new-claims.json。"
    )
    s = new("cover", 1, "把高频判断，交给更快的决策模型",
            "Strands Decider × Jev × GPT：快速选择与复杂推理协作，让各类模型承担适合的工作。", cover_notes)
    L.rect(s, .68, 2.18, 6.02, 4.21, L.WHITE, L.LINE, True)
    V.icon(s, "timer", .92, 2.42, V.PURPLE, .42)
    L.text(s, "Jev 官方发布数据", 1.52, 2.43, 4.8, .34, 20, V.PURPLE, True)
    L.text(s, "70–500 ms", .93, 3.04, 5.5, .82, 46, V.PURPLE, True)
    L.text(s, "端到端响应时间", .98, 3.97, 4.9, .30, 19, L.INK)
    L.text(s, "40–200×", .94, 4.62, 3.8, .70, 39, V.PURPLE, True)
    L.text(s, "官方比较：System One 查询\n相对其比较的大模型更快",
           1.0, 5.44, 5.15, .62, 16, L.MUTED)
    link(s, "TypeSafe / Jev 官方发布文 ↗", JEV_URL, 1.0, 6.06, 5.1, 12.5, V.PURPLE)
    L.text(s, "决策模型是大模型的补充", 7.10, 2.34, 5.4, .52, 26, L.TEAL, True)
    for y, title, body, icon, color in (
        (3.10, "决策模型：快速选择", "分流、路由、工具、下一步动作\n输出候选 ID / 真值 / 评分", "list-filter", L.TEAL),
        (4.84, "大模型：理解、规划与生成", "处理复杂异常、代码与开放问题\n完成内容，再交给决策环节判断", "brain-circuit", L.BLUE),
    ):
        L.rect(s, 7.12, y, 5.50, 1.41, L.WHITE, L.LINE, True)
        V.icon(s, icon, 7.33, y + .20, color, .37)
        L.text(s, title, 7.87, y + .17, 4.45, .36, 19, color, True)
        L.text(s, body, 7.35, y + .73, 4.99, .56, 16, L.INK)
    L.line(s, [(9.85, 4.54), (9.85, 4.81)], L.MUTED, 1.7, True)

    # P8: customer figures stay attributed to the user's customer case.
    customer_notes = (
        "本页把通用适用场景落到已实现客户项目与潜在需求。"
        "\n已实现：自动驾驶客户的Jev智能驾驶意图识别微调方案，用户提供口径为"
        "RLCD范式DPO+LoRA，choice准确率90%，latency100ms。"
        "这些数字由用户提供，本轮未访问客户原始测试集，也未重跑该模型；"
        "样本数量、延迟分位数、端到端边界、硬件和测试集划分未提供，不能自行补写。"
        "不将该客户方案称作Decider微调结果，也不据此宣称公开Jev API开放权重或支持用户直接LoRA。"
        "\n技术路径示意：围绕客户输入与固定意图选项形成偏好样本，"
        "用DPO目标与LoRA适配领域选择，再以固定选项的choice准确率及延迟检查效果。"
        "RLCD为该客户方案的范式表述；RLCD不等同于DPO或LoRA。"
        "任务是驾驶相关意图识别，不是本项目已验证车辆控制。"
        "\n潜在：智能家居设备根据用户输入，在固定动作和流程集合中选择。"
        "本页示例动作和流程是说明性设计，不是实际设备执行记录，也没有编造性能或准确率。"
        "\n本项目支付客服、模型路由、图片与工具选择的原有实测在后续页展开。"
        "\n来源：sources/new-claims.json，2026-10-26用户提供的客户信息。"
    )
    s = new("cases", 8, "适用场景：固定选项、高频触发、快速响应",
            "从模型路由、客服选择，延伸到客户意图识别与智能设备动作编排。", customer_notes)
    L.rect(s, .68, 2.18, 6.0, 3.82, L.WHITE, L.LINE, True)
    L.label(s, "已实现 · 客户项目", .93, 2.40, 2.22, L.PALE_TEAL, L.TEAL, 12)
    V.icon(s, "route", 5.85, 2.38, L.TEAL, .40)
    L.text(s, "自动驾驶：智能驾驶意图识别", .94, 2.99, 5.43, .45, 24, L.TEAL, True)
    L.text(s, "Jev 领域微调 · RLCD 范式\nDPO + LoRA：用客户偏好样本适配固定意图",
           .96, 3.62, 5.38, .86, 17, L.INK)
    for x, value, label in ((.98, "90%", "choice 准确率"), (3.73, "100 ms", "latency")):
        L.text(s, value, x, 4.73, 2.35, .61, 34, L.TEAL, True)
        L.text(s, label, x + .02, 5.40, 2.35, .26, 14, L.MUTED)
    L.text(s, "客户提供的测试结果", .99, 5.74, 5.1, .23, 11.5, L.MUTED)
    L.rect(s, 6.93, 2.18, 5.72, 3.82, L.WHITE, L.LINE, True)
    L.label(s, "潜在场景", 7.18, 2.40, 1.47, L.PALE_BLUE, L.BLUE, 12)
    V.icon(s, "workflow", 11.86, 2.38, L.BLUE, .40)
    L.text(s, "智能家居：选择动作与流程", 7.19, 2.99, 5.06, .45, 24, L.BLUE, True)
    L.text(s, "设备根据当前用户输入，从固定选项中决策。",
           7.20, 3.66, 4.98, .63, 17, L.INK)
    for y, label, value in (
        (4.47, "用户输入", "“我要休息了”"),
        (4.96, "固定动作", "调暗灯光 / 关闭电视 / 调节空调"),
        (5.45, "固定流程", "执行睡眠场景 / 追问 / 暂不执行"),
    ):
        L.text(s, label, 7.22, y, 1.18, .32, 14, L.BLUE, True)
        L.text(s, value, 8.44, y, 3.91, .34, 14, L.INK)
    V.callout(s, "技术思路：业务输入 + 固定候选 → 快速选择 → 执行或交给下一环节", y=6.18, size=16)

    # P32: compare management responsibilities, not incomparable benchmark runs.
    comparison_notes = (
        "本页承接vLLM并发：推理引擎和Agent运行体系是不同层。"
        "直接EC2部署强调模型服务与推理参数控制；AgentCore Runtime Instances把Agent入口、"
        "执行身份、session与实例生命周期纳入AWS运行体系，并可组合观测、评估与协作协议。"
        "二者均可运行GPU模型，不把AgentCore说成替代GPU或天然更低延迟。"
        "直接EC2也可以接入OpenTelemetry与评估器，观测和评估并非GPU Runtime的独占能力。"
        "\n本项目原演示文字路径为ECS→g6.xlarge/L4 vLLM；"
        "独立验证是InvokeAgentRuntime→AgentCore管理的g5.xlarge/A10G原生CUDA。"
        "实例、引擎、样本、调用路径不同，不能计算两者加速倍数。"
        "本轮未实现AgentCore内的vLLM，未来可做相同输入和硬件下的适配与回归。"
        "\nAgentCore官方支持HTTP、MCP、A2A协议；必须实现协议服务并配置Runtime，"
        "不会把普通HTTP handler自动转换为A2A。Identity/Gateway/Memory等按需组合，"
        "不表示本次都已部署。业务用户身份与状态隔离仍由应用正确绑定。"
        "\n依据：sources/AGENTCORE_GPU.md；" + AWS + "runtime-instances-how-it-works.html；"
        + AWS + "runtime-a2a.html。"
    )
    s = new("compare", 32, "从 EC2 模型服务，进入 AgentCore 运行体系",
            "vLLM 优化推理与批处理；AgentCore 组织 Agent 的运行、协作、观测和评估。", comparison_notes)
    V.table(s, ["维度", "直接 EC2 部署", "AgentCore Runtime on GPU EC2"], [
        ["调用入口", "应用自行维护模型 HTTP API", "IAM 保护的 InvokeAgentRuntime"],
        ["GPU 与生命周期", "自行维护实例、进程和驱动", "Capacity Provider 管理 EC2 与 session"],
        ["会话与上下文", "应用自行设计", "原生 session + 应用业务会话"],
        ["Agent / 工具交互", "自行接入协议与身份", "HTTP / MCP / A2A；按需组合服务"],
        ["观测与效果验证", "自行接入 OpenTelemetry / 评估", "Observability + Evaluations"],
        ["本项目已验证", "g6.xlarge · vLLM 文字推理", "g5.xlarge · 原生 CUDA · 8 业务会话"],
    ], [1.91, 4.96, 5.13], x=.68, y=2.24, row_h=.53, size=14)
    V.callout(s, "选择运行体系，再选择推理引擎：AgentCore 与 vLLM 可以组合适配。", y=6.24, size=16)

    # P33: editable AWS diagram sized for projection, plus the full diagram asset.
    architecture_notes = (
        "这是已验证架构的演示简图，完整SVG/PNG保存在assets/agentcore-gpu-architecture.*，"
        "本页均使用AWS官方服务图标。受信任客户端通过IAM InvokeAgentRuntime进入GPU Runtime；"
        "所有验证决策在AgentCore管理的g5.xlarge内部执行，未走原g6/vLLM服务。"
        "\nPython3.11直接部署：约32.9MB代码ZIP，CUDA依赖与原始v19权重来自固定digest，"
        "S3全部PublicAccessBlock开启，执行角色读取固定制品并SHA-256校验。"
        "模型缓存放EBS的home目录；/tmp为tmpfs，不作大模型缓存。"
        "验证实际为公网子网、安全组无入站规则，不把公网子网误标为私有子网。"
        "\n一个(Capacity Provider,runtimeSessionId)对应一台EC2；"
        "8个是应用内business_session_id，不是8个原生session，也不是8个独立Agent进程。"
        "owner来自可信调用方，状态暂存进程内存，不能声称操作系统租户隔离或状态已持久化。"
        "原生CUDA forward使用GPU锁串行，模型加载一次。"
        "\n24次请求成功；单并发16次模型均值80.216ms、Runtime往返209.48ms；"
        "8并发8次模型81.82ms、队列269.817ms、往返477.013ms。均为预热后小样本，"
        "不是持续吞吐，也不能与前面不同硬件/批次的vLLM比较倍数。"
        "验证GPU已释放，未新增运行实例。"
        "\n来源：sources/agentcore-summary.json；sources/AGENTCORE_GPU.md；" + AC_DOC
    )
    s = new("architecture", 33, "GPU 仍在 EC2，AgentCore 统一调用与会话",
            "已验证：Python 3.11 直接部署 · us-east-1 / 按需 g5.xlarge · Decider 原生 CUDA。", architecture_notes)
    L.rect(s, 2.58, 2.19, 10.07, 3.55, L.WHITE, "879196")
    aws_icon(s, "cloud", 2.78, 2.34, .31)
    L.text(s, "AWS · us-east-1", 3.20, 2.35, 3.2, .30, 15, L.NAVY, True)
    L.rect(s, 5.53, 2.82, 4.05, 2.66, "FFFBF5", "ED7100")
    aws_icon(s, "ec2", 5.73, 3.03, .45)
    L.text(s, "AgentCore 管理的 GPU EC2", 6.32, 3.09, 3.05, .36, 16, "BE721F", True)
    for x, w, y, h, title, body, icon, color in (
        (.70, 1.59, 3.14, 1.42, "业务入口", "可信身份\n业务任务", "workflow", L.INK),
        (2.85, 2.10, 3.12, 1.46, "Runtime", "IAM 调用\n共享原生 session", "agentcore", L.TEAL),
        (9.98, 2.39, 3.12, 1.46, "Observability", "CloudWatch\n输入 / 输出 / 耗时", "cloudwatch", V.PURPLE),
    ):
        L.rect(s, x, y, w, h, L.WHITE, L.LINE, True)
        if icon in ("agentcore", "cloudwatch"):
            aws_icon(s, icon, x + .14, y + .14, .30)
        else:
            V.icon(s, icon, x + .14, y + .14, color, .30)
        L.text(s, title, x + .55, y + .16, w - .64, .32, 14, color, True)
        L.text(s, body, x + .15, y + .68, w - .28, .60, 13.5, L.INK)
    L.text(s, "8 个业务会话\nowner 校验 · 请求幂等",
           5.76, 3.74, 3.62, .63, 15, L.INK, True)
    L.text(s, "Decider 2B · 模型加载一次\n原生 CUDA forward 串行",
           5.77, 4.46, 3.53, .65, 16, L.TEAL, True)
    for points in (
        [(2.30, 3.87), (2.81, 3.87)],
        [(4.96, 3.87), (5.48, 3.87)],
        [(9.60, 3.87), (9.96, 3.87)],
    ):
        L.line(s, points, L.MUTED, 1.5, True)
    aws_icon(s, "s3", 2.98, 4.93, .36)
    L.text(s, "私有 S3 · 代码与权重", 3.47, 4.93, 2.00, .50, 13, L.INK)
    L.line(s, [(4.96, 5.22), (5.48, 5.22)], "BE721F", 1.5, True)
    L.text(s, "A10G 24 GB · 公网子网 / 无入站规则",
           5.74, 5.20, 3.65, .23, 10.5, L.MUTED)
    link(s, "完整架构图 ↗", AC_DOC, 10.06, 5.02, 2.3, 12)
    for x, w, value, label in (
        (.75, 2.8, "1 GPU / 8 会话", "24 次成功 · 1 原生 session"),
        (3.80, 2.6, "80 ms", "单并发模型均值"),
        (6.74, 2.6, "209 ms", "单并发 Runtime 往返"),
        (9.65, 2.82, "477 ms", "8 并发 Runtime 往返"),
    ):
        L.text(s, value, x, 6.02, w, .37, 23, L.TEAL, True)
        L.text(s, label, x + .02, 6.46, w, .23, 11.7, L.MUTED)

    # P34: native A2A interoperability is a documented target, not fabricated evidence.
    collaboration_notes = (
        "本页是下一阶段目标，不是本轮已经完成的A2A验证。"
        "AWS官方支持在AgentCore Runtime托管A2A server：通过Agent Card发布技能、"
        "标准JSON-RPC提交任务并返回消息、任务状态或产物，使用SigV4或OAuth2.0认证。"
        "AWS SDK的serve_a2a可处理健康检查、Agent Card和协议入口；"
        "需要实现Decider对应的executor并配置A2A Runtime，普通HTTP handler不会自动变成A2A。"
        "本项目延续IAM签名接入，不要求采用Cognito。"
        "\n目标流程：客服/编排Agent发现决策Agent技能，提交事实与候选；"
        "Decider返回结构化选择；执行Agent或应用工具完成动作，再将实际结果关联到trace。"
        "有限choice/noul/score作为A2A消息或artifact内容，不声称Decider成为生成式规划器。"
        "\nAgentCore服务按需组合：Runtime承载Agent与GPU；Identity对接出入站身份；"
        "Gateway接入工具，Browser/Code Interpreter按业务需要调用；Memory提供跨轮持久记忆；"
        "Observability关联traces；Evaluations评价选择与执行效果。"
        "当前仅GPU Runtime、IAM调用、Observability和按需Evaluations验证，"
        "A2A、Identity服务、Gateway、Memory及工具执行尚未接入。"
        "\n另一个官方能力：多个互信Runtime可共享CapacityProvider+runtimeSessionId，"
        "部署到同一EC2并共享文件系统/GPU；本轮仅一个Runtime进程，"
        "8个应用业务会话不能当成8个Agent共同部署的证据。"
        "\n依据：" + AWS + "runtime-a2a.html；"
        + AWS + "runtime-instances-how-it-works.html。"
    )
    s = new("collaboration", 34, "让决策 Agent 与其他 Agent 原生协作",
            "目标方案：A2A 发现技能、提交任务、返回选择与结果；运行服务按需组合。", collaboration_notes)
    L.label(s, "A2A 接入待实施", .74, 2.17, 2.10, L.PALE_BLUE, L.BLUE, 12)
    for x, title, body, icon, color in (
        (.74, "客服 / 编排 Agent", "发现 Agent Card\n提交业务事实与候选", "headset", L.BLUE),
        (4.95, "GPU 决策 Agent", "AgentCore Runtime + Decider\n返回候选 ID / 真值 / 评分", "list-filter", L.TEAL),
        (9.16, "执行 Agent / 业务工具", "执行选定动作\n返回实际业务结果", "workflow", V.PURPLE),
    ):
        L.rect(s, x, 2.83, 3.43, 1.42, L.WHITE, L.LINE, True)
        V.icon(s, icon, x + .17, 3.02, color, .34)
        L.text(s, title, x + .67, 3.02, 2.62, .34, 16.5, color, True)
        L.text(s, body, x + .19, 3.59, 3.05, .55, 14, L.INK)
    L.line(s, [(4.20, 3.52), (4.90, 3.52)], L.BLUE, 1.5, True)
    L.line(s, [(8.42, 3.52), (9.10, 3.52)], L.TEAL, 1.5, True)
    L.text(s, "A2A", 4.25, 3.10, .64, .28, 12, L.BLUE, True, PP_ALIGN.CENTER)
    L.text(s, "选择", 8.46, 3.10, .60, .28, 12, L.TEAL, True, PP_ALIGN.CENTER)
    L.text(s, "AgentCore 运行体系", .76, 4.55, 5.5, .36, 20, L.NAVY, True)
    services = [
        ("Runtime", "GPU 与 session · 已验证", L.TEAL),
        ("Identity", "身份与授权 · 按需接入", L.BLUE),
        ("Gateway / 执行工具", "API、Browser、Code Interpreter · 待接入", L.BLUE),
        ("Memory", "跨轮持久记忆 · 按需接入", L.BLUE),
        ("Observability", "真实 traces 与性能 · 已验证", L.TEAL),
        ("Evaluations", "按需效果评分 · 已验证", L.TEAL),
    ]
    for i, (title, body, color) in enumerate(services):
        x, y = .76 + (i % 3) * 4.08, 5.10 + (i // 3) * .71
        L.rect(s, x, y, 3.83, .61, L.PALE_TEAL if color == L.TEAL else L.PALE_BLUE, radius=True)
        L.text(s, title, x + .12, y + .07, 3.56, .27, 14.5, color, True)
        L.text(s, body, x + .12, y + .36, 3.56, .19, 10.5, L.MUTED)

    # P35: actual Evaluate results, separated from planned training and promotion.
    evaluation_notes = (
        "本页所有已完成结果来自2026-10-26AgentCore GPU验证。"
        "24次实际决策产生24个invoke_agent decider spans和24个decider.gpu.evaluate spans。"
        "evaluate.py从CloudWatch读取真实spans，按business_session_id和traceId过滤，"
        "每种场景取一个实际trace，并在evaluationReferenceInputs附预先固定的期望choice。"
        "调用AgentCore原生Evaluate API，evaluatorId=Builtin.Correctness。"
        "\n四条：payment-confirmed settle、payment-pending wait、"
        "support-payment payments、support-security security，均label Correct、value1.0。"
        "不是代码伪造的评分，也不把4条小样本等同于模型100%通用准确率。"
        "这里只验证分类与支付状态选择，没有实际执行交易，因此Correct不等于业务动作成功。"
        "\n当前按需脚本触发，评估耗时不计入在线决策延迟；"
        "48spans是24次请求的两层trace，不是48个独立任务。"
        "feedback.jsonl根据独立业务参考导出不一致记录，本批为空；"
        "没有自动改条件、微调或发布，也没有把评估低分自动全部纳入训练集。"
        "\n下一步增加工具执行结果和最终业务状态，必要时配置自定义业务评估器，"
        "人工核验坏例→上下文/判断条件优化或微调→独立回归集→发布新版本。"
        "这样把前面的两条优化路线接到持续效果验证。"
        "\n来源：sources/agentcore-evaluations.json、sources/agentcore-observability.json、"
        "sources/agentcore-summary.json；" + AWS + "getting-started-on-demand.html。"
    )
    s = new("evaluation", 35, "用 AgentCore Evaluations 验证决策效果",
            "真实调用 → traces + 参考答案 → 按需评估；下一步纳入工具执行结果和业务完成状态。", evaluation_notes)
    for x, title, body, icon, color in (
        (.74, "收集真实调用", "24 次决策 · 48 spans", "chart-no-axes-combined", L.TEAL),
        (4.95, "评估实际选择", "Builtin.Correctness", "clipboard-check", V.PURPLE),
        (9.16, "4 / 4 Correct", "4 类场景各抽样 1 条", "circle-check", L.TEAL),
    ):
        L.rect(s, x, 2.22, 3.43, 1.03, L.WHITE, L.LINE, True)
        V.icon(s, icon, x + .16, 2.38, color, .31)
        L.text(s, title, x + .60, 2.37, 2.65, .33, 17, color, True)
        L.text(s, body, x + .20, 2.88, 3.01, .25, 13, L.MUTED)
    for x in (4.23, 8.44):
        L.line(s, [(x, 2.74), (x + .65, 2.74)], L.MUTED, 1.5, True)
    V.table(s, ["实际输入摘要", "Decider 选择", "参考动作", "评估返回"], [
        ["100 USDC 匹配订单，12/12 确认", "settle", "settle", "Correct · 1.0"],
        ["金额匹配，仅 3/12 确认", "wait", "wait", "Correct · 1.0"],
        ["客服：USDC 到账延迟", "payments", "payments", "Correct · 1.0"],
        ["客服：未经授权登录、密码被改", "security", "security", "Correct · 1.0"],
    ], [4.82, 2.35, 2.35, 2.48], x=.68, y=3.55, row_h=.43, size=14)
    L.rect(s, .72, 6.02, 11.9, .49, L.PALE_TEAL, radius=True)
    L.text(s, "后续改进：核验错误样本 → 调整上下文 / 微调 → 独立回归 → 发布",
           .92, 6.12, 11.47, .29, 16, L.TEAL, True)

    # P40: keep the original destinations and add the public AgentCore documentation.
    resources_notes = (
        "演示、原始记录、vLLM、微调、JevBench与AgentCore方案入口。"
        "AgentCore使用主项目的公开文档链接，独立实现仓库StrandsDeciderAgentCore当前私有，"
        "访问代码需要仓库权限。文档已注明A2A协作为后续方案、基础GPU与Evaluation已验证。"
        "下一页延续internal only的候选顺序诊断，外部分享前应按听众范围处理。"
    )
    s = new("links", 40, "打开演示，查看真实过程和原始记录",
            "业务实测、领域微调、vLLM 推理与 AgentCore 运行方案，分别保留可追溯证据。", resources_notes)
    destinations = [
        ("在线演示", V.DEMO, V.DEMO),
        ("主项目", V.REPO, V.REPO),
        ("vLLM 适配与公共 API", V.VREPO, V.VREPO),
        ("场景验证与截图", V.REPO + "/blob/main/validation/README.md", "StrandsDecider / validation ↗"),
        ("AgentCore GPU 方案", AC_DOC, "架构、会话、Observability 与 Evaluations ↗"),
        ("Decider 微调过程与结果", "https://github.com/milan9527/StrandsDeciderTetrisFinetuning",
         "StrandsDeciderTetrisFinetuning ↗"),
        ("Strands Decider 官方介绍", "https://strandsagents.com/blog/introducing-strands-decider/",
         "strandsagents.com / introducing-strands-decider ↗"),
        ("JevBench 评测与方法", "https://benchmarkheaven.com/jev-models/api",
         "benchmarkheaven.com / jev-models / api ↗"),
    ]
    for i, (label, url, shown) in enumerate(destinations):
        y = 2.18 + i * .56
        L.text(s, label, .77, y, 3.42, .34, 17, L.NAVY, True)
        link(s, shown, url, 4.31, y + .015, 8.1, 15)

    order = []
    mapping = []
    for old, node in enumerate(original_ids, 1):
        key = "cover" if old == 1 else "links" if old == 35 else None
        if key:
            order.append(generated[key]["id"])
            mapping.append({**generated[key]["meta"], "source": "v23_reworked", "v23_page": old})
        else:
            order.append(node)
            mapping.append({**previous["slides"][old - 1], "source": "v23", "v23_page": old,
                            "content_modified": False})
        if old == 7:
            order.append(generated["cases"]["id"])
            mapping.append(generated["cases"]["meta"].copy())
        if old == 30:
            for key in ("compare", "architecture", "collaboration", "evaluation"):
                order.append(generated[key]["id"])
                mapping.append(generated[key]["meta"].copy())
    keep = {node.rId for node in order}
    for node in list(prs.slides._sldIdLst):
        prs.slides._sldIdLst.remove(node)
        if node.rId not in keep:
            prs.part.drop_rel(node.rId)
    for node in order:
        prs.slides._sldIdLst.append(node)
    metas = []
    for index, (slide, meta) in enumerate(zip(prs.slides, mapping, strict=True), 1):
        if meta["source"] == "v23":
            slide.notes_slide.notes_text_frame.text = notes_remap(
                slide.notes_slide.notes_text_frame.text, meta["v23_page"])
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            if Inches(.30) <= shape.top <= Inches(.36) and shape.text:
                replace_text(shape, shape.text, chapter(index))
            if shape.top > Inches(7) and re.fullmatch(r"\d+ / \d+", shape.text):
                replace_text(shape, shape.text, f"{index:02} / {PAGES}")
        meta.update(page=index, section=chapter(index), notes=slide.notes_slide.notes_text_frame.text)
        assert len(meta["notes"]) <= 2600, (index, len(meta["notes"]))
        metas.append(meta)
    assert len(prs.slides) == PAGES
    prs.core_properties.title = "Strands Decider：快速决策与 AgentCore GPU 运行方案"
    prs.core_properties.subject = "v24 / 41 slides / customer cases, GPU Runtime, A2A roadmap and evaluation"
    prs.core_properties.modified = datetime.now(UTC)
    target = OUT / f"{NAME}.pptx"
    prs.save(target)
    assert V.digest(BASE) == base_hash
    save(OUT / "build-manifest.json", {
        "created_at": datetime.now(UTC).isoformat(), "pages": PAGES,
        "base_file": str(BASE.relative_to(ROOT)), "base_sha256": base_hash,
        "pptx_file": str(target.relative_to(ROOT)), "pptx_sha256": V.digest(target),
        "new_pages": NEW_PAGES, "modified_pages": MODIFIED_PAGES, "slides": metas,
        "evidence": V.EVIDENCE, "internal_only_pages": [41],
        "retained_body_count": 34, "new_model_calls": 0, "new_training_jobs": 0,
        "new_deployments": 0, "github_push": False,
        "claim_categories": ["official_vendor_claim", "user_supplied_customer_result",
                             "measured_agentcore_result", "planned_integration"],
        "a2a_deployed": False,
    })
    lines = ["# v24 讲稿与来源", "",
             "基于 v23：重写首页、补充适用场景，在 vLLM 之后加入四页 AgentCore，更新资料页。", ""]
    for meta in metas:
        lines += [f'## P{meta["page"]} · {meta["title"]}', "", meta["notes"], ""]
    (OUT / "讲稿与来源.md").write_text("\n".join(lines))
    (OUT / "README.md").write_text(
        "# v24 · 快速决策与 AgentCore GPU 运行方案\n\n"
        f"[PPTX](./{NAME}.pptx) · [PDF](./{NAME}.pdf) · "
        "[全篇预览](./slides-overview.png) · [更新页预览](./updated-overview.png) · "
        "[讲稿与来源](./讲稿与来源.md) · [核验记录](./render-audit.json)\n\n"
        "**基于 v23，共 41 页。** 原 34 页正文与图片保留；首页和资料页重写，新增 5 页。\n\n"
        "| 页码 | 内容 |\n| --- | --- |\n"
        "| P1 | Jev 官方 70–500 ms、40–200× 数据；决策模型补充大模型 |\n"
        "| P2–7 | 趋势、原理、BERT / 决策模型 / GPT、JevBench |\n"
        "| P8 | 已实现：Jev 智能驾驶意图识别微调；潜在：智能家居固定动作与流程 |\n"
        "| P9–23 | 演示架构、支付、模型路由、图片及其他场景结果 |\n"
        "| P24–28 | Decider 领域微调过程与效果 |\n"
        "| P29–31 | vLLM 部署与并发实测 |\n"
        "| P32–35 | EC2 对比、AgentCore GPU 架构、A2A 协作目标、Evaluation 实测 |\n"
        "| P36–38 | Jev、EC2 On-Demand 与一年 RI 成本 |\n"
        "| P39–41 | 选型、资料与 internal only 附录 |\n\n"
        "首页数字明确为 Jev 厂商发布数据，并保留可点击来源；客户案例的 90% / 100 ms "
        "来自用户提供的客户项目结果，未在本项目重测。AgentCore 已验证 GPU Runtime、"
        "8 个业务会话、48 spans 与 4 条 Correct 评估；A2A、Memory、Gateway 等为待接入方案。"
        "不把不同模型、硬件或调用路径的耗时混算。\n\n"
        "[完整 AgentCore 架构图](./assets/agentcore-gpu-architecture.svg) · "
        "[新增事实与归属](./sources/new-claims.json) · [构建清单](./build-manifest.json)\n\n"
        "本版没有新增模型调用、微调任务、云资源或 GitHub 推送。原 v23 保留不变。"
        "P41 延续 internal only 标注。\n"
    )
    (OUT / "调整说明.md").write_text(
        "# v24 调整说明\n\n"
        "1. 首页突出快速决策：Jev 官方 70–500 ms 与 40–200× 比较，说明与大模型互补。\n"
        "2. P8 在模型原理与榜单之后、演示架构之前介绍适用场景；加入客户意图识别微调与智能家居需求。\n"
        "3. P32–35 承接 vLLM：从推理引擎到 Agent 运行体系，再到 GPU 架构、A2A 协作与效果验证。\n"
        "4. 原支付、路由、图片、其他验证、微调、并发与成本页正文和截图保留，页码与章节顺延。\n"
        "5. P40 新增公开 AgentCore 方案入口。P41 保留 internal only。\n\n"
        "已验证、官方能力、客户提供结果及后续方案在正文和讲稿中分别注明。"
        "新增页不恢复用户已删除的页脚说明行。\n"
    )
    print(json.dumps({"pptx": str(target), "pages": PAGES, "new_pages": NEW_PAGES}, ensure_ascii=False))


if __name__ == "__main__":
    main()
