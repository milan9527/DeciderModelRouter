"""Extend v18 with five evidence-backed Tetris fine-tuning slides.

Build only: no model calls, GPU jobs, deployment, or GitHub publication.
Original diagram assets and experiment reports are retained with SHA-256s.
"""

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime

import build_presentation_v10 as V
import presentation_layout as L
from build_presentation_v5 import replace_text
from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

ROOT = V.ROOT
PROJECT = ROOT.parent / "StrandsDeciderTetrisFinetuning"
OUT = ROOT / "docs/presentations/v19"
BASE = next((ROOT / "docs/presentations/v18").glob("*.pptx"))
NAME = "Strands-Decider-v19-领域微调过程与实测结果-39页-20261020"
PAGES = 39
REPO = "https://github.com/milan9527/StrandsDeciderTetrisFinetuning"
WALK = REPO + "/blob/main/docs/TETRIS_FINETUNING_WALKTHROUGH.zh.md"
RESULTS = REPO + "/blob/main/finetuning/RESULTS.md"


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def rounded(s, x, y, w, h, title, body, color=L.TEAL, fill=L.WHITE, size=15):
    L.rect(s, x, y, w, h, fill, L.LINE, True)
    L.text(s, title, x + .16, y + .13, w - .32, .34, 17, color, True)
    L.text(s, body, x + .16, y + .65, w - .32, h - .70, size)


def arrow(s, points, color=L.MUTED, dashed=False):
    L.line(s, points, color, 1.7, arrow=True, dashed=dashed)


def replace_in(slide, old, new):
    matched = 0
    for shape in slide.shapes:
        if shape.has_text_frame and old in shape.text:
            replace_text(shape, old, new)
            matched += 1
    assert matched == 1, (old, matched)


def main():
    for part in ("assets", "sources"):
        (OUT / part).mkdir(parents=True, exist_ok=True)
    V.OUT, V.ASSETS, V.SOURCES = OUT, OUT / "assets", OUT / "sources"
    V.EVIDENCE.clear()
    V.IMAGES.clear()
    V.SLIDES.clear()
    base_hash = V.digest(BASE)
    previous = V.load(BASE.parent / "build-manifest.json")
    provenance = []
    source_paths = [
        "docs/TETRIS_FINETUNING_WALKTHROUGH.zh.md", "finetuning/RESULTS.md",
        "reports/2026-10-16/summary.json", "reports/2026-10-16/training.json",
        "reports/2026-10-16/job-status.json", "reports/2026-10-16/data/manifest.json",
        "reports/2026-10-16/data/audit.json", "reports/2026-10-16/baseline/report.json",
        "reports/2026-10-16/adapted/report.json",
        "reports/2026-10-16/replay-verification.json",
        "docs/diagrams/tetris-sft-workflow.png", "docs/diagrams/tetris-sft-workflow.svg",
        "docs/diagrams/lora-training-step.png", "docs/diagrams/lora-training-step.svg",
    ]
    for relative in source_paths:
        source = PROJECT / relative
        target = OUT / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        provenance.append({
            "source": str(source), "url": REPO + "/blob/main/" + relative,
            "snapshot": str(target.relative_to(ROOT)), "sha256": V.digest(target),
        })
        V.record(target)
    git_revision = subprocess.check_output(
        ["git", "-C", str(PROJECT), "rev-parse", "HEAD"], text=True,
    ).strip()
    source_root = OUT / "sources/reports/2026-10-16"
    summary = V.load(source_root / "summary.json")
    training = V.load(source_root / "training.json")
    stages = V.load(source_root / "job-status.json")
    splits = V.load(source_root / "data/manifest.json")["splits"]
    adapted = V.load(source_root / "adapted/report.json")
    baseline = V.load(source_root / "baseline/report.json")
    replay = V.load(source_root / "replay-verification.json")
    assert summary["models"]["baseline"]["choices"] == baseline["heldout_choices"]
    assert summary["models"]["adapted"]["choices"] == adapted["heldout_choices"]
    assert all(g["raw_choice_matches_applied_placement"] and g["board_replay_matches"]
               for g in replay["games"])
    assert len(replay["games"]) == 20
    assert sum(g["verified_live_moves"] for g in replay["games"]) == 895
    assert training["completed_steps"] == 787 and training["config"]["epochs"] == 1
    assert round(next(row["seconds"] for row in stages["stages"] if row["stage"] == "train") / 60) == 64

    prs = Presentation(BASE)
    original_ids = list(prs.slides._sldIdLst)
    order, metas = [], []

    def keep(page):
        order.append(original_ids[page - 1])
        metas.append({
            **previous["slides"][page - 1], "source": "v18", "v18_page": page,
            "body_modified": page in (1, 23, 32, 33),
        })
        return prs.slides[page - 1]

    def new(title, subtitle, notes):
        slide = V.base(prs, "06  领域微调 · 俄罗斯方块实测", title, subtitle, "", notes)
        order.append(prs.slides._sldIdLst[-1])
        metas.append({**V.SLIDES[-1], "source": "new", "body_modified": True})
        return slide

    for page in range(1, 24):
        slide = keep(page)
        if page == 1:
            replace_in(slide,
                       "Strands Decider、Jev 与 GPT：从原理、业务验证，到 vLLM 部署与成本选择",
                       "Strands Decider、Jev 与 GPT：原理、业务验证、领域微调、vLLM 部署与成本")
        if page == 23:
            replace_in(slide, "把失败样本留下，再决定是否需要微调",
                       "从输入优化走向领域微调：用失败样本定义目标")
            replace_in(slide, "06  后续优化路线", "06  从业务诊断到领域微调")
            old_notes = slide.notes_slide.notes_text_frame.text
            old_notes = old_notes.replace(
                "下列为后续方案；尚未完成领域微调，也未宣称新的准确率或生产收益。",
                "本页保留浏览器、支付与模型路由的优化建议。随后五页展示另一个任务——"
                "俄罗斯方块——已完成的领域微调；不表示支付或路由模型已使用该检查点。",
            )
            slide.notes_slide.notes_text_frame.text = old_notes
            metas[-1]["title"] = "从输入优化走向领域微调：用失败样本定义目标"

    # 24: Reflow the original portrait workflow into a readable, editable slide.
    s = new(
        "微调闭环：从可验证标签，到独立测试和完整对局",
        "俄罗斯方块：根据棋盘、当前方块、下一块和候选后果，选择一个落点。",
        "流程依据项目 tetris-sft-workflow.svg 横向重排，原图完整保存在本版 sources/docs/diagrams。"
        "2026-10-16 首轮 GPU SFT；标签由引擎的六项指标严格支配关系生成。"
        "共 18,868 条题，按种子和可观察棋盘隔离四分区，同一局面的候选及措辞变体留在同区。"
        "train 更新参数；validation 选 best；calibration 拟合温度；test 只做最终评估。"
        "训练后用新种子进行 20 局游戏，共 895 次落块回放；推理时由模型选落点，程序执行。"
        "微调检查点另存，部署为单独步骤，不由训练脚本自动发布；未替换支付或路由演示模型。\n"
        f"全过程：{WALK}\n原始记录：sources/reports/2026-10-16/",
    )
    steps = [
        ("01  生成标签", "模拟合法落点\n计算六项指标", "list-filter"),
        ("02  审计与分区", "重算候选与标签\n隔离棋盘和种子", "clipboard-check"),
        ("03  GPU 微调", "续训 LoRA + 决策头\n验证集选择 best", "brain-circuit"),
        ("04  评估与对局", "校准 → 测试 → 回放\n检查点另存后部署", "workflow"),
    ]
    V.flow(s, steps, y=2.20, h=1.89)
    split_specs = [
        ("train", "训练", "更新参数", L.TEAL),
        ("validation", "验证", "选择 best", L.BLUE),
        ("calibration", "校准", "拟合温度", V.PURPLE),
        ("test", "测试", "最终效果", L.ORANGE),
    ]
    for i, (key, label, use, color) in enumerate(split_specs):
        x = .75 + 3.035 * i
        L.rect(s, x, 4.50, 2.74, 1.46, L.WHITE, L.LINE, True)
        L.text(s, label, x + .17, 4.64, 2.40, .30, 16, color, True)
        L.text(s, f'{splits[key]["rows"]:,}', x + .17, 5.02, 2.40, .44, 27, color, True)
        L.text(s, use, x + .17, 5.56, 2.40, .26, 13.5, L.MUTED)
    V.hyperlink(s, "查看微调全过程与原始流程图 ↗", WALK, .78, 6.16, 8.0, 14)

    # 25: A documented real training item, not a fabricated model response.
    s = new(
        "标签怎么来：用一条真实训练题说明“更好的落点”",
        "当前 Z，下一块 T；三个候选均能生成下一块，下表比较其余五项指标。",
        "真实样本：训练 JSONL 第 267 行，seed=10001、step=27，当前 Z、下一块 T，"
        "此前消行 7。示例由微调全过程文档记录；本页是带标签的训练输入，不是模型推理返回。"
        "选项 p676=(x3,r0,y13)，p172=(x0,r0,y13)，p852=(x6,r1,y15)。"
        "三个候选均可生成下一块，p852 其余五项指标也均优于其他候选，因此 label=2。"
        "六项指标全不差且至少一项更好才生成严格支配标签；各有取舍时不强行给唯一答案。"
        "标签仅表示本题候选和单步指标下的优势，不能证明所有未来序列上的最优落点。"
        "随机候选 ID、排序及训练 collator 再次换序时，标签同步重映射。\n"
        f"来源：{WALK}#33-一条实际样本",
    )
    # Draw the five occupied bottom rows as an explanatory board from the source.
    board = [".####.....", ".####.....", "######.###", "######..##", "######.###"]
    L.rect(s, .70, 2.20, 2.43, 3.77, L.WHITE, L.LINE, True)
    L.text(s, "当前棋盘 · 底部五行", .86, 2.40, 2.10, .36, 14, L.NAVY, True)
    cell = .191
    for row, line in enumerate(board):
        for col, value in enumerate(line):
            L.rect(s, .94 + col * cell, 3.04 + row * cell, cell - .015, cell - .015,
                   L.TEAL if value == "#" else "E9EEF4")
    L.text(s, "上方 15 行为空\n\n输入：棋盘 + 候选后果\n监督目标：候选下标", .88, 4.37,
           2.08, 1.36, 14, L.MUTED)
    V.table(s, ["选项", "消行 ↑", "空洞 ↓", "最高列 ↓", "总高度 ↓", "起伏 ↓"],
            [["p676", "0", "4", "7", "42", "14"],
             ["p172", "0", "4", "7", "42", "10"],
             ["p852", "1", "3", "4", "31", "8"]],
            [1.35, 1.48, 1.48, 1.48, 1.48, 1.48], x=3.48, y=2.20,
            row_h=.53, size=15, highlights=(2,))
    rounded(s, 3.48, 4.56, 4.42, 1.40, "正确标签：第三个候选",
            "p852 在六项指标上支配其他选项。\nlabel = 2（下标从 0 开始）", size=14)
    rounded(s, 8.12, 4.56, 4.55, 1.40, "换序时，标签跟着落点走",
            "随机命名 + 随机排列 + 标签重映射。\n训练它比较事实，减少位置依赖。", L.BLUE, size=14)

    # 26: Keep the source diagram semantics, reflow to landscape native shapes.
    s = new(
        "模型怎么更新：冻结基座，续训 LoRA 与决策头",
        "监督微调（SFT）：冻结 Qwen3.5-2B，续训多层 LoRA 与决策头；下图简化展示一个目标层。",
        "依据 lora-training-step.svg 横向重排。原图完整保留在 sources/docs/diagrams。"
        "Qwen3.5-2B 文本基座有 24 层、hidden=2048。原始矩阵冻结，已有 LoRA 的 A/B 继续训练；"
        "W有效=W基座+(alpha/r)BA。r=16、alpha=32、dropout=0.05；图中仅展示一个目标层。"
        "LoRA 分布于全注意力、线性注意力和前馈网络目标矩阵。"
        f"本轮可训练 LoRA 参数 {training['trainable_lora_parameters']:,}，"
        f"pointer head 参数 {training['trainable_head_parameters']:,}。"
        "head 使用整道题和候选向量为当前候选打分，softmax 输出概率。"
        "损失 -log P(正确候选)，梯度累积后由 AdamW 更新 LoRA 与 head。"
        "基座仍参与前向/反向，但原矩阵不应用权重更新。"
        "没有训练文字答案生成，没有 GPT 教师，也没有 PPO/GRPO；训练开始与结束的散列确认两组权重改变。"
        "bf16 基座、fp32 head、梯度检查点；batch=2，累积=8，1 epoch，787 次更新。"
        "LoRA/head 学习率 2e-5/1e-4；完整训练子进程 3842.60 秒，约 64 分钟。\n"
        f"来源：{WALK}#5-lora-和多层模型在这里如何工作",
    )
    rounded(s, .70, 3.01, 2.08, 1.22, "棋盘与候选", "分词 → 向量", L.BLUE, size=15)
    rounded(s, 3.23, 2.25, 3.25, 1.22, "基座原矩阵 · 冻结", "W0x，原权重保持不变", L.MUTED, "EEF2F6", 15)
    rounded(s, 3.23, 3.91, 3.25, 1.28, "已有 LoRA · 续训", "修正量 (α/r) × B(Ax)", L.TEAL, L.PALE_TEAL, 15)
    L.text(s, "+", 6.68, 3.33, .32, .44, 29, L.BLUE, True, PP_ALIGN.CENTER)
    rounded(s, 7.29, 3.01, 2.12, 1.28, "Pointer 决策头", "续训候选打分", L.TEAL, L.PALE_TEAL, 15)
    rounded(s, 10.00, 3.01, 2.62, 1.28, "候选概率与损失", "L = −log P(正确项)", L.BLUE, size=15)
    arrow(s, [(2.80, 3.61), (3.03, 3.61), (3.03, 2.86), (3.20, 2.86)], L.BLUE)
    arrow(s, [(3.03, 3.61), (3.03, 4.54), (3.20, 4.54)], L.BLUE)
    arrow(s, [(6.50, 2.86), (6.84, 2.86), (6.84, 3.28)], L.BLUE)
    arrow(s, [(6.50, 4.54), (6.84, 4.54), (6.84, 3.81)], L.BLUE)
    arrow(s, [(7.04, 3.63), (7.26, 3.63)], L.BLUE)
    arrow(s, [(9.44, 3.63), (9.97, 3.63)], L.BLUE)
    arrow(s, [(11.31, 4.32), (11.31, 5.52), (4.84, 5.52), (4.84, 5.22)], L.ORANGE, True)
    arrow(s, [(8.35, 5.52), (8.35, 4.32)], L.ORANGE, True)
    L.text(s, "标签损失反向传播，只更新绿色模块", 7.00, 5.63, 5.25, .33, 14, L.ORANGE, True)
    L.text(s, "单张 L4 24GB", .77, 6.06, 3.5, .35, 18, L.TEAL, True)
    L.text(s, "1 epoch · 787 次更新", 4.64, 6.06, 3.9, .35, 18, L.TEAL, True)
    L.text(s, "完整训练约 64 分钟", 8.85, 6.06, 3.8, .35, 18, L.TEAL, True)

    # 27: The values come from raw JSON; distinguish validation from test.
    s = new(
        "独立测试：准确率提高，候选换序后的选择也更稳定",
        "同一批 2,068 道测试题；按种子与棋盘隔离，不参与参数更新或 best 选择。",
        "结果来自 2026-10-16 summary.json、baseline/report.json、adapted/report.json，"
        "原版 v19 与 SFT 在完全相同 test 分区比较。原顺序 SFT 答对 2067/2068；"
        "反序并改名答对 2066/2068；实际选择一致 2065/2068。"
        "一致率表示同一道题两种排列选到同一实际落点，不是两模型动作一致。"
        "验证曲线使用另一个 2120 条 validation 分区，只用来选 best；两者最终 99.95% 是巧合，不能混同。"
        "温度由 2090 条 calibration 选择，本轮 T=1.0；正温度不改变候选排序。"
        "本次同时使用 LoRA/head 更新和排列增强，未做消融，不能单独归因。"
        "只证明当前引擎支配比较题分布的效果；没有复测全部其他能力，"
        "不将该准确率用于支付、模型路由、JevBench 或三模型总体排名。\n"
        f"报告：{RESULTS}\n训练：sources/reports/2026-10-16/training.json",
    )
    L.text(s, "验证集：选择最佳检查点", .74, 2.16, 4.1, .38, 18, L.NAVY, True)
    history = [(0, training["baseline_validation"]["accuracy"])]
    history += [(h["step"], h["validation"]["accuracy"]) for h in training["history"]]
    V.table(s, ["更新步数", "验证准确率"],
            [[f"{step:,}", f"{accuracy * 100:.2f}%"] for step, accuracy in history],
            [1.86, 2.12], x=.74, y=2.75, row_h=.43, size=15, highlights=(4,))
    L.text(s, "best：第 787 步\n校准温度：1.0", .91, 5.64, 3.71, .73, 16, L.TEAL, True)
    L.text(s, "测试集：原版 vs 微调版", 5.07, 2.16, 7.55, .38, 18, L.NAVY, True)
    a = summary["models"]["baseline"]["choices"]
    b = summary["models"]["adapted"]["choices"]
    measures = [
        ("原顺序准确率", a["canonical"]["accuracy"], b["canonical"]["accuracy"]),
        ("换序并改名准确率", a["reversed_and_renamed"]["accuracy"], b["reversed_and_renamed"]["accuracy"]),
        ("换序后实际落点一致率", a["same_actual_choice_fraction"], b["same_actual_choice_fraction"]),
    ]
    V.table(s, ["测试指标", "原始 v19", "SFT"],
            [[label, f"{before * 100:.2f}%", f"{after * 100:.2f}%"]
             for label, before, after in measures],
            [3.69, 1.93, 1.97], x=5.07, y=2.75, row_h=.62, size=16)
    rounded(s, 5.07, 5.38, 7.59, 1.07, "SFT：2,067 / 2,068 题答对",
            "效果与稳定性均改善；结论限于本次俄罗斯方块比较题。", size=13.5)

    # 28: Play the actual selected moves; do not equate choice accuracy with games.
    s = new(
        "完整对局：微调版 10/10 局达到 50 块上限",
        "每个模型使用相同 5 个新种子 × 2 种输入；程序执行模型选择，并逐步回放核验。",
        "完整对局不使用训练或测试的种子。每个模型在 50000–50004 上，"
        "数字/自然语言各五局，每局最多 50 块，总共 20 局。"
        "保留原应用的候选分组、初选和决选，实际执行与原始模型选择一致，"
        "没有用启发式替换模型选择，895 次落块全部通过回放。"
        "原版数字 0/5 到达上限、平均消行 1.8；SFT 数字 5/5、12.2。"
        "原版自然语言 1/5、3.0；SFT 自然语言 5/5、14.2。"
        "SFT 两种输入仍分别有 17/250 指标劣选，不等同于长期策略错误；"
        "比较题高准确率没有覆盖有取舍的全部游戏状态。"
        "这是一次训练、五个新种子的短局证据，不推断无限存活或通用业务能力。"
        "项目后来提供统一 finetune.py 入口，其真实 CPU smoke 不等于重跑本批 GPU；"
        "本页所有效果和约64分钟训练时间仍来自首轮GPU实验。\n"
        f"项目：{REPO}\n结果：{RESULTS}\n"
        "回放：sources/reports/2026-10-16/replay-verification.json",
    )
    for i, (policy, label) in enumerate((("original", "数字描述"), ("model", "自然语言描述"))):
        x = .72 + i * 6.10
        L.rect(s, x, 2.22, 5.88, 3.69, L.WHITE, L.LINE, True)
        L.text(s, label, x + .20, 2.40, 5.45, .37, 20, L.NAVY, True)
        L.text(s, "平均消行", x + .20, 2.93, 5.45, .30, 14, L.MUTED)
        for j, (model, name, color) in enumerate(
            (("baseline", "原始 v19", L.BLUE), ("adapted", "SFT", L.TEAL))
        ):
            game = summary["models"][model]["games"][policy]
            y = 3.47 + .70 * j
            L.text(s, name, x + .20, y, 1.17, .31, 15, color, True)
            L.rect(s, x + 1.50, y + .02, game["mean_lines"] / 16 * 3.45, .27, color)
            L.text(s, f'{game["mean_lines"]:.1f}', x + 1.50 + game["mean_lines"] / 16 * 3.45 + .12,
                   y - .03, .64, .38, 20, color, True)
        before = summary["models"]["baseline"]["games"][policy]
        after = summary["models"]["adapted"]["games"][policy]
        L.text(s, f'达到 50 块：{before["reached_piece_limit"]}/5 → {after["reached_piece_limit"]}/5',
               x + .20, 5.07, 5.40, .39, 20, L.TEAL, True)
        L.text(s, f'平均落块：{before["mean_pieces"]:.1f} → {after["mean_pieces"]:.1f}',
               x + .20, 5.57, 5.40, .26, 13.5, L.MUTED)
    L.text(s, "本批短局表现明显改善；微调版仍有 34/500 次指标劣选，后续需验证更长对局。",
           .79, 6.10, 11.93, .35, 16, L.TEAL, True)

    for page in range(24, 35):
        slide = keep(page)
        if page == 32:
            replace_in(
                slide, "优先：分类、工具选择、分流。\n\n低单次延迟、可控自托管。\n稳定流量再评估 1 年 RI。\n证据与完成判断需补强。",
                "优先：分类、工具选择、分流。\n\n低单次延迟、可控自托管。\n领域微调可改善特定任务。\n稳定流量再评估 1 年 RI。",
            )
            slide.notes_slide.notes_text_frame.text += (
                "\nv19 增补：Tetris 独立比较题 60.35%→99.95%，换序后实际落点一致率"
                "35.44%→99.85%；限本任务，不替代支付、路由或其他业务验收，也不重排三模型名次。"
            )
        if page == 33:
            # Fit the extra source link in the content area, not the removed footnote row.
            for shape in slide.shapes:
                if shape.has_text_frame and Inches(2.20) <= shape.top < Inches(6.10):
                    shape.top = Inches(2.23 + (shape.top / 914400 - 2.25) * .90)
            L.text(slide, "Decider 微调过程与结果", .80, 5.98, 3.20, .34, 17, L.TEAL, True)
            V.hyperlink(slide, "StrandsDeciderTetrisFinetuning · 流程图、训练与评估 ↗",
                        WALK, 4.05, 5.98, 8.45, 15)
            slide.notes_slide.notes_text_frame.text += f"\n微调项目：{REPO}\n全过程：{WALK}\n结果：{RESULTS}"

    for node in list(prs.slides._sldIdLst):
        prs.slides._sldIdLst.remove(node)
    for node in order:
        prs.slides._sldIdLst.append(node)
    assert len(prs.slides) == PAGES
    for index, (slide, meta) in enumerate(zip(prs.slides, metas, strict=True), 1):
        for shape in list(slide.shapes):
            if shape.has_text_frame and Inches(6.5) <= shape.top < Inches(7.03):
                assert not shape.text.strip(), (index, shape.text)
                shape._element.getparent().remove(shape._element)
            elif shape.has_text_frame and shape.top > Inches(7) and re.fullmatch(r"\d+ / \d+", shape.text):
                replace_text(shape, shape.text, f"{index:02} / {PAGES}")
        meta.update(page=index, notes=slide.notes_slide.notes_text_frame.text)
    prs.core_properties.title = "Strands Decider × Jev × GPT：领域微调过程与实测结果"
    prs.core_properties.subject = "v19 / 39 pages / Tetris SFT process and measured outcomes"
    prs.core_properties.modified = datetime.now(UTC)
    target = OUT / f"{NAME}.pptx"
    prs.save(target)
    assert V.digest(BASE) == base_hash
    save(OUT / "build-manifest.json", {
        "created_at": datetime.now(UTC).isoformat(), "pages": PAGES,
        "base_file": str(BASE.relative_to(ROOT)), "base_sha256": base_hash,
        "pptx_file": str(target.relative_to(ROOT)), "pptx_sha256": V.digest(target),
        "new_pages": [24, 25, 26, 27, 28],
        "modified_existing_pages": [1, 23, 37, 38],
        "internal_only_pages": [39], "new_model_calls": 0, "new_training_jobs": 0,
        "source_repo": REPO, "source_repo_head": git_revision,
        "source_snapshots": provenance, "evidence": V.EVIDENCE,
        "diagram_adaptation": "Original diagram content reflowed as editable landscape shapes; originals retained.",
        "slides": metas, "lower_explanatory_rows": 0,
    })
    lines = ["# v19 讲稿与来源", "", "新增 P24–28：俄罗斯方块领域微调过程与实测结果。",
             "基于 v18；原有业务与性能实验保持各自采集口径。末页 P39 保留 internal only。",
             "完整原始报告和两张流程图保存在 sources，PPT 备注仅放简短讲解与来源链接。",
             "继承页讲稿中的旧页码按 build-manifest.json 的 v18_page 对应。", ""]
    for meta in metas:
        lines += [f'## {meta["page"]:02} · {meta["title"]}', "", meta["notes"], ""]
    (OUT / "讲稿与来源.md").write_text("\n".join(lines))
    (OUT / "README.md").write_text(
        "# v19 · 领域微调过程与实测结果\n\n"
        f"[PPTX](./{NAME}.pptx) · [PDF](./{NAME}.pdf) · "
        "[新增页预览](./updated-overview.png) · [讲稿与来源](./讲稿与来源.md)\n\n"
        "基于最新 v18，新增 **5 页，共 39 页**。在场景验证与优化建议之后、vLLM 部署之前，"
        "加入已完成的俄罗斯方块领域微调实验；保留原有支付、路由、三模型性能、JevBench 和成本对比。\n\n"
        "| 页码 | 内容 |\n| --- | --- |\n"
        "| P24 | 从引擎标签、数据分区到训练、独立测试与完整对局 |\n"
        "| P25 | 真实训练题：候选指标、严格支配标签和换序增强 |\n"
        "| P26 | 冻结基座，续训 LoRA 与 pointer head；L4 完整训练约 64 分钟 |\n"
        "| P27 | 原版 / 微调版独立测试：准确率 60.35% → 99.95%，换序落点一致率 35.44% → 99.85% |\n"
        "| P28 | 完整对局：微调版 10/10 达到 50 块，数字 / 自然语言平均消行 12.2 / 14.2 |\n\n"
        "封面、优化章节衔接、选型总结和资料页同步补充微调内容。"
        "**P39 保留原有 internal only 标记**；本次未推送 GitHub。\n\n"
        "两张原流程图按投影阅读需要横向重排为可编辑 PPT 图形，"
        "原 PNG / SVG 完整保留于 [图表来源](./sources/docs/diagrams/)。"
        "新增页不恢复此前删除的页底说明；详细边界和来源放在讲稿与备注。\n\n"
        f"来源：[微调全过程]({WALK}) · [实验结果]({RESULTS}) · [示例仓库]({REPO})。\n\n"
        "本版引用 2026-10-16 已完成的 GPU 实验，没有重新训练或调用线上模型。"
        "2,068 题准确率只覆盖引擎支配比较题，完整对局为五个新种子和 50 块上限；"
        "微调版仍有 34/500 次指标劣选，不能外推为支付、路由或通用模型的新成绩。\n\n"
        "构建：`scripts/build_presentation_v19.py`。"
        "检查：`scripts/verify_presentation_v19.py`。"
        "复核清单：[构建来源](./build-manifest.json) · [渲染与数据核验](./render-audit.json)。\n"
    )
    print(json.dumps({"pptx": str(target), "pages": PAGES, "new_pages": [24, 25, 26, 27, 28]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
