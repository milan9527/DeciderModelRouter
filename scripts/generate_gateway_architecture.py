"""Draw the independent gateway with local, official AWS SVG icons.

SVG generation uses the standard library. --png also needs Playwright/Chromium
and a Chinese font, for example Noto Sans CJK SC.
"""

import argparse
from pathlib import Path
from xml.etree import ElementTree as ET

from generate_diagrams import aws_icon

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/diagrams/decider-model-router-aws.zh.svg"
WIDTH, HEIGHT = 1920, 1530
INK, MUTED = "#232f3e", "#526171"
PURPLE, ORANGE, BLUE = "#693bc5", "#b85a00", "#245da8"


def build():
    svg = ET.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "width": str(WIDTH),
            "height": str(HEIGHT),
            "viewBox": f"0 0 {WIDTH} {HEIGHT}",
            "role": "img",
            "aria-labelledby": "diagram-title diagram-description",
        },
    )
    ET.SubElement(
        svg, "title", id="diagram-title"
    ).text = "DeciderModelRouter — 独立模型网关 AWS 部署架构"
    ET.SubElement(svg, "desc", id="diagram-description").text = (
        "浏览器通过专用 CloudFront 访问私有 S3 前端，API 经 VPC Origin、"
        "内网 ALB 进入私有 ECS Fargate。后端独立调用 g5.xlarge GPU 获取七项任务属性，"
        "结合镜像内证据快照选择 Bedrock 目标模型。两张 DynamoDB 表保存认证、会话、"
        "评测和性能，独立私有 S3 保存媒体。CloudFront 不直接读取媒体桶。"
        "GPU 不接收媒体字节，评测记录不会自动更新选型快照。"
    )
    defs = ET.SubElement(svg, "defs")
    colors = {"request": MUTED, "gpu": ORANGE, "model": PURPLE, "data": BLUE}
    for key, color in colors.items():
        marker = ET.SubElement(
            defs,
            "marker",
            id=f"arrow-{key}",
            markerWidth="9",
            markerHeight="9",
            refX="8",
            refY="4.5",
            orient="auto-start-reverse",
            markerUnits="userSpaceOnUse",
        )
        ET.SubElement(marker, "path", d="M0,0 L9,4.5 L0,9 Z", fill=color)
    ET.SubElement(svg, "rect", width=str(WIDTH), height=str(HEIGHT), fill="white")
    g = ET.SubElement(
        svg,
        "g",
        {"font-family": "Noto Sans CJK SC, Microsoft YaHei, sans-serif", "fill": INK},
    )

    def rect(x, y, w, h, stroke="#c7d0d9", fill="white", dashed=False):
        attrs = dict(x=str(x), y=str(y), width=str(w), height=str(h), stroke=stroke, fill=fill)
        attrs["stroke-width"] = "1.6"
        if dashed:
            attrs["stroke-dasharray"] = "8 6"
        return ET.SubElement(g, "rect", attrs)

    def text(x, y, value, size=22, color=INK, weight="400", anchor="start"):
        node = ET.SubElement(
            g,
            "text",
            {
                "x": str(x),
                "y": str(y),
                "font-size": str(size),
                "fill": color,
                "font-weight": weight,
                "text-anchor": anchor,
            },
        )
        node.text = value
        return node

    icon_counter = 0

    def icon(name, x, y, size=64):
        nonlocal icon_counter
        icon_counter += 1
        g.append(aws_icon(name, f"gateway-{icon_counter}", x, y, size))

    def arrow(points, kind="request", both=False):
        attrs = {
            "points": " ".join(f"{x},{y}" for x, y in points),
            "fill": "none",
            "stroke": colors[kind],
            "stroke-width": "2.5",
            "stroke-linejoin": "round",
            "marker-end": f"url(#arrow-{kind})",
        }
        if both:
            attrs["marker-start"] = f"url(#arrow-{kind})"
        ET.SubElement(g, "polyline", attrs)

    def service(name, x, y, w, h, title, lines):
        rect(x, y, w, h)
        icon(name, x + 20, y + 22)
        text(x + 103, y + 47, title, 24, weight="600")
        for index, line in enumerate(lines):
            text(x + 103, y + 81 + 29 * index, line, 20, MUTED)

    # AWS group boundaries: CloudFront is global; managed data services are
    # regional but outside the VPC. The private-subnet box is a logical group,
    # not a claim that the deployment fits in one subnet or is highly available.
    text(40, 64, "DeciderModelRouter", 38, weight="700")
    text(40, 105, "独立模型网关 · AWS 部署架构", 25, MUTED)
    text(1875, 65, "部署状态  2026-11-25", 20, MUTED, anchor="end")
    text(1875, 103, "静态前端 / API / GPU 分离", 21, MUTED, anchor="end")
    ET.SubElement(g, "path", d="M40,132 H1880", stroke="#ff9900", **{"stroke-width": "4"})
    rect(275, 160, 1605, 1115, "#879196")
    icon("cloud", 293, 169, 34)
    text(340, 195, "AWS Cloud", 23, weight="600")
    rect(300, 375, 1555, 875, "#00a4a6", dashed=True)
    icon("region", 318, 390, 30)
    text(360, 416, "AWS Region · us-east-1", 22, weight="600")
    rect(335, 450, 950, 750, "#8c4fff", "#fcfaff")
    icon("vpc", 351, 461, 30)
    text(395, 486, "专用 Amazon VPC", 22, weight="600")
    rect(360, 505, 900, 670, "#00a4a6", "#f4fbfa")
    icon("subnet", 376, 519, 28)
    text(416, 543, "私有子网（逻辑汇总）", 20, weight="500")

    # Browser and global entry point.
    icon("client", 110, 222, 64)
    text(142, 320, "浏览器工作台", 24, weight="600", anchor="middle")
    arrow([(188, 254), (360, 254)])
    text(235, 239, "HTTPS", 18, MUTED)
    service(
        "cloudfront",
        360,
        215,
        630,
        120,
        "Amazon CloudFront · 专用分配",
        ["d3eipg2g3l3an3.cloudfront.net"],
    )
    text(52, 415, "前端展示", 23, weight="600")
    for i, line in enumerate(
        [
            "用户登录与管理",
            "自由输入 / 场景模板",
            "分类与选型依据",
            "目标输出与阶段耗时",
            "会话历史 / 效果评测",
        ]
    ):
        text(52, 457 + 37 * i, line, 19, MUTED)
    text(52, 720, "自建认证", 22, weight="600")
    text(52, 755, "scrypt 密码哈希", 19, MUTED)
    text(52, 788, "服务端登录会话", 19, MUTED)
    text(52, 910, "连线说明", 22, weight="600")
    for i, (kind, label) in enumerate(
        [
            ("request", "入口与静态资源"),
            ("gpu", "GPU 属性判断"),
            ("model", "目标模型执行"),
            ("data", "数据持久化"),
        ]
    ):
        y = 951 + 46 * i
        arrow([(52, y), (89, y)], kind)
        text(101, y + 7, label, 18, MUTED)

    # Independent CloudFront origins. The media bucket is never this origin.
    arrow([(990, 269), (1590, 269), (1590, 465)])
    text(1130, 247, "OAC · 签名读取私有 S3", 21, MUTED)
    text(1630, 327, "/ 与 /gateway/*", 19, MUTED)
    text(1630, 359, "根域名直接打开", 19, MUTED)
    service(
        "s3",
        1370,
        465,
        440,
        135,
        "Amazon S3 · 静态前端",
        ["私有桶 · OAC 授权", "默认首页 gateway/index.html"],
    )
    arrow([(670, 335), (670, 563), (500, 563), (500, 601)])
    text(707, 365, "/api/* · 不缓存", 20, MUTED)
    text(707, 401, "CloudFront VPC Origin", 20, MUTED)

    # Private compute. The routing module and its snapshots live in the API.
    icon("alb", 468, 605, 64)
    text(500, 710, "Application", 22, weight="600", anchor="middle")
    text(500, 744, "Load Balancer", 22, weight="600", anchor="middle")
    text(500, 782, "内网 ALB", 20, MUTED, anchor="middle")
    arrow([(545, 639), (730, 639)])
    text(611, 622, "API", 19, MUTED, anchor="middle")
    rect(730, 590, 500, 260, "#ed7100")
    icon("ecs", 752, 612, 56)
    icon("fargate", 821, 612, 56)
    text(899, 638, "Amazon ECS", 24, weight="600")
    text(899, 672, "AWS Fargate · 私有 API", 22, weight="500")
    text(752, 711, "FastAPI · 认证 / GPU 调用 / 选型 / 目标执行", 20, MUTED)
    rect(750, 735, 460, 93, "#d8dee6", "#f7f8fa")
    text(768, 767, "镜像内的版本化 JSON 证据快照", 20, weight="500")
    text(768, 798, "Bedrock 目录 / 榜单 / 价格 / 校准配置", 19, MUTED)

    arrow([(940, 850), (940, 955)], "gpu", both=True)
    text(962, 891, "私网 HTTP + Bearer", 20, ORANGE)
    text(962, 924, "任务描述 ⇄ 七项属性", 20, ORANGE)
    service(
        "ec2",
        730,
        955,
        500,
        165,
        "Amazon EC2 · g5.xlarge",
        ["A10G 24GB · 独立 GPU 服务", "Decider / Liquid / H2O", "每次调用一个分类器 · 默认 H2O"],
    )
    text(390, 1002, "GPU 仅识别任务属性", 21, weight="600")
    text(390, 1038, "输入：文字、附件类型、输出要求", 19, MUTED)
    text(390, 1072, "不传媒体字节、型号或价格", 19, MUTED)
    text(390, 1148, "当前：1 个 API 任务 + 1 台 GPU；网络出口与运维路径省略。", 19, MUTED)

    # Backend-only access to target models and account-isolated persistence.
    arrow([(1230, 678), (1370, 678)], "model", both=True)
    service(
        "bedrock",
        1370,
        665,
        440,
        155,
        "Amazon Bedrock",
        ["IAM 授权 · 所选型号与配置", "接收原始任务与真实附件", "返回结果 / 用量 / 错误"],
    )
    arrow([(1230, 717), (1315, 717), (1315, 943), (1370, 943)], "data")
    arrow([(1315, 943), (1315, 1150), (1370, 1150)], "data")
    text(1335, 850, "IAM · 持久化", 19, BLUE)
    service(
        "dynamodb",
        1370,
        870,
        440,
        170,
        "Amazon DynamoDB",
        ["认证表：用户 / 登录 / 执行凭据", "业务表：会话 / 事件 / 评测", "阶段耗时 / Token / 费用"],
    )
    service(
        "s3",
        1370,
        1080,
        440,
        140,
        "Amazon S3 · 媒体",
        ["专用私有桶 · 附件及生成文件", "经登录后的 API 读写"],
    )

    # Explain the split of responsibilities without implying an online
    # calibration loop or measured multimodal quality/cost optimization.
    text(40, 1330, "一次任务的主要流程", 25, weight="600")
    steps = [
        ("01", "任务与用户约束"),
        ("02", "GPU 返回七项属性"),
        ("03", "ECS 依据证据选型"),
        ("04", "Bedrock 按需执行"),
        ("05", "保存结果与性能"),
    ]
    for i, (number, label) in enumerate(steps):
        x = 40 + 376 * i
        rect(x, 1353, 344, 64, "#d8dee6", "#f7f8fa")
        text(x + 16, 1393, number, 23, ORANGE, weight="600")
        text(x + 60, 1393, label, 22, weight="500")
        if i < len(steps) - 1:
            arrow([(x + 349, 1385), (x + 369, 1385)])
    text(
        40,
        1455,
        "文本/向量：可比评测与参考费用；多模态：能力与适配器过滤后按固定顺序选择。业务校准当前未启用。",
        20,
        MUTED,
    )
    text(
        40,
        1493,
        "阶段事件沿 API 原链路回传；历史评测不会自动更新选型快照。AWS 官方图标：2026-07-31 发布包。",
        19,
        MUTED,
    )
    ET.indent(svg, space="  ")
    return ET.tostring(svg, encoding="unicode", xml_declaration=False) + "\n"


def render_png(svg_path, scale):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=scale,
        )
        page.goto(svg_path.as_uri())
        page.evaluate("document.fonts.ready")
        page.locator("svg").first.screenshot(path=str(svg_path.with_suffix(".png")))
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--png", action="store_true", help="Also render PNG with Chromium")
    parser.add_argument("--scale", type=float, default=2, help="PNG pixels per SVG unit")
    args = parser.parse_args()
    OUTPUT.write_text(build(), encoding="utf-8")
    print(OUTPUT.relative_to(ROOT))
    if args.png:
        if args.scale <= 0:
            parser.error("--scale must be positive")
        render_png(OUTPUT, args.scale)
        print(OUTPUT.with_suffix(".png").relative_to(ROOT))


if __name__ == "__main__":
    main()
