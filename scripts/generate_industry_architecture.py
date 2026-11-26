"""Generate the industry agent's AWS architecture with repository-owned official icons."""

from pathlib import Path
from xml.etree import ElementTree as ET

from generate_diagrams import BLUE, ORANGE, TEAL, Page

ROOT = Path(__file__).resolve().parents[1]


def build():
    p = Page("行业 Agent × Model Router", "industry-agent",
             "AgentCore Runtime V2 · 每次模型调用动态路由 · 原生工具与多轮历史",
             width=1680, height=1160, aws_style=True)
    p.box("cloud", 230, 140, 1410, 930, "AWS Cloud · 独立服务", group=True, icons=("cloud",))
    p.box("browser", 20, 315, 180, 190, "行业工作台\n四轮模板 / 自由追问\n过程与历史", icons=("client",), size=18)
    p.box("cf", 265, 310, 250, 190, "CloudFront\n专用域名 / HTTPS\nAPI禁缓存", icons=("cloudfront",), size=20)
    p.box("s3", 265, 695, 250, 180, "Amazon S3\n私有前端桶 / OAC", icons=("s3",), size=20)
    p.box("ecs", 630, 455, 340, 235, "ECS Fargate\n内网 ALB / 自建登录\n任务管理 / 约束校验 / 执行\n原生消息 / 缓存 / 回退", icons=("ecs", "fargate"), size=20)
    p.box("agent", 1080, 210, 470, 220, "AgentCore Runtime V2\nStrands Agent · model-router\nquery_records / calculate_metric / read_policy\n合成业务记录 · 只读工具", icons=("agentcore",), size=20)
    p.box("bedrock", 1120, 505, 370, 190, "Amazon Bedrock\n按证据选择的目标模型\nConverse + toolUse / toolResult", icons=("bedrock",), size=20)
    p.box("gpu", 635, 825, 335, 185, "Amazon EC2 · g5.xlarge\nLiquid d1-2B / A10G\n任务判断 + 依据评测选模", icons=("ec2",), size=20)
    p.box("db", 1120, 825, 400, 190, "Amazon DynamoDB\n账户 / 会话 / Agent任务\n每次模型调用 / 工具 / 性能", icons=("dynamodb",), size=20)
    p.edge("browser", "cf", [(200, 395), (265, 395)], "HTTPS", at=(232, 375))
    p.edge("cf", "s3", [(390, 500), (390, 695)], "OAC签名读取", at=(390, 612))
    p.edge("cf", "ecs", [(515, 405), (570, 405), (570, 540), (630, 540)],
           "VPC Origin", at=(570, 460))
    p.edge("ecs", "agent", [(830, 455), (830, 305), (1080, 305)],
           "IAM / SigV4 调用", color=BLUE, at=(944, 280))
    p.edge("agent", "cf", [(1080, 235), (995, 235), (995, 195), (390, 195), (390, 310)],
           "模型回调：HTTPS + 短期任务凭据", color=TEAL, at=(700, 195))
    p.edge("ecs", "bedrock", [(970, 582), (1120, 582)], "原生工具协议", color=TEAL, at=(1045, 559))
    p.edge("ecs", "gpu", [(790, 690), (790, 825)], "私网 GPU HTTP", color=ORANGE, at=(790, 760))
    p.edge("ecs", "db", [(970, 660), (1040, 660), (1040, 900), (1120, 900)],
           "按用户持久保存", color=BLUE, at=(1156, 777))
    p.box("note", 30, 1080, 1610, 55,
          "Runtime不直接调用Bedrock；完整成功对话从数据库恢复。模板不预选目标型号，切换次数不作为质量通过率。",
          text=True, size=19)
    svg = p.svg()
    children = list(svg)
    for index, node in enumerate(children[:-1]):
        if node.tag == "rect" and node.get("width") == "240" and node.get("height") == "26":
            label = children[index + 1]
            width = sum(14 if ord(c) > 127 else 8 for c in label.text) + 20
            node.set("width", str(width))
            node.set("x", str(float(label.get("x")) - width / 2))
    return svg


if __name__ == "__main__":
    path = ROOT / "docs/diagrams/industry-agent-aws.zh.svg"
    ET.indent(svg := build())
    ET.ElementTree(svg).write(path, encoding="unicode")
    print(path)
