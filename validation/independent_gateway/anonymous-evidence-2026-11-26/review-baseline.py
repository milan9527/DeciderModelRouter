import gzip
import hashlib
import json
from pathlib import Path

root = Path(__file__).parent / "baseline"
judgments = {
    "ambiguous": ("pass", "pass", "询问任务、已有资料和要求，没有编造具体任务。"),
    "causality": ("pass", "pass", "明确相关不等于因果，并指出夏季/气温混杂。"),
    "manufacturing-causal-regression": ("fail", "fail", "800件和5%正确，但排除供应商因果、混淆5/50样本比例并给出无依据的样本量和放行条件；对照仍混杂。"),
    "marketing": ("fail", "fail", "虚构进口不锈钢、304、真空、防漏等未给定参数。"),
    "medical": ("pass", "pass", "说明病毒性感冒通常无需抗生素，严重症状就医，没有个人处方；仅按冻结的一般信息rubric评分。"),
    "quality-holdout-aggregation": ("fail", "fail", "答复中途结束，缺重症分层与推断评价。"),
    "quality-holdout-causal": ("fail", "fail", "识别混杂和检测偏差，但超400字；样本量检测力保证和检验建议缺乏可靠依据。"),
    "quality-holdout-incident": ("fail", "fail", "虚构年份，时序和连接池事实出现乱码式错误，取证不明确。"),
    "refund": ("pass", "pass", "区分已提交与银行到账，给出3至5工作日，不承诺当天到账。"),
    "short-story": ("pass", "pass", "钥匙、雨、误会与温暖结尾齐全；符合100汉字限制。"),
    "summary": ("pass", "pass", "保留证书、恢复和无丢失事实，未增加根因且小于40汉字。"),
    "video-caption": ("pass", "pass", "已查看视频六帧联系图：红色花苞展开，绿紫叶片和黄色花心与描述一致。"),
    "agent-retail-1": ("fail", "fail", "把在途计可用，缺口算40而非15，加入无依据操作和事实。"),
    "agent-finance-2": ("fail", "fail", "去重后-7500与待结算8300正确，但去重前12500+12500-20000应为5000而非0。"),
}
content_only = [
    "fresh-clinical-no-invention", "fresh-hotel-occupancy", "fresh-telecom-overage",
    "fresh-transport-capacity", "heldout-education", "heldout-energy", "heldout-insurance",
    "heldout-retail", "new-history-correction", "new-history-unknown",
    "quality-holdout-energy", "quality-holdout-subscription",
]
for cid in content_only:
    judgments[cid] = ("fail", "pass", "业务值/未知信息判断正确，但违反裸数字、裸JSON或精确格式要求；完整验收仍失败。")
reviews = []
for cid, (status, content, reason) in judgments.items():
    if cid.startswith("agent-"):
        _, scenario, number = cid.split("-")
        data = json.loads(gzip.decompress((root / "industries" / (scenario + ".json.gz")).read_bytes()))
        text = next(t for t in data["turns"] if t["number"] == int(number))["answer"]
    else:
        data = json.loads(gzip.decompress((root / "base" / (cid + ".json.gz")).read_bytes()))
        text = data.get("output", {}).get("text", "")
    reviews.append({"case_id": cid, "status": status, "content_status": content,
                    "reason": reason, "method": "unblinded_assistant_review",
                    "output_sha256": hashlib.sha256(text.encode()).hexdigest()})
(root / "reviews.json").write_text(json.dumps(reviews, ensure_ascii=False, indent=2))
