"""Freeze independent business inputs before evaluating any tuned checkpoint."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).parent
suite = json.loads(Path("validation/independent_gateway/quality-priority-2026-11-26/suite.json").read_text())
for case in suite["cases"]:
    case["split"] = "development_regression"
new = [
    ("factory-rework", "制造", "某产线首检480件，不合格36件；其中24件返工后合格，其余12件隔离。首检不合格率不得用返工结果改写。只输出JSON，键first_pass_defect_percent,final_good,isolated。",
     {"first_pass_defect_percent": 7.5, "final_good": 468, "isolated": 12}, "high"),
    ("claims-cap", "保险", "保单规则：认可费用先扣免赔额1200元，余款报销80%，单次赔付上限9000元。认可费用15000元。只输出JSON，键after_deductible,uncapped_payment,payment，单位元。",
     {"after_deductible": 13800, "uncapped_payment": 11040, "payment": 9000}, "medium"),
    ("warehouse-reserve", "物流", "仓库现有900件，已锁定订单260件，质检冻结80件，两者不重叠。明日到货200件尚未入库。新订单需求600件。只输出JSON，键available_now,shortage_now；单位件。",
     {"available_now": 560, "shortage_now": 40}, "low"),
    ("payment-dedup", "支付", "结算事件以event_id去重，相同ID为重复投递。记录为E7:+8400，E7:+8400，E8:-1500，E9:+3000，单位分。只输出JSON，键raw_total,deduplicated_total。",
     {"raw_total": 18300, "deduplicated_total": 9900}, "high"),
    ("power-tariff", "能源", "工厂用电：谷时1200kWh每度0.32元，平时800kWh每度0.65元，峰时400kWh每度1.10元。暂不计税费。只输出JSON，键energy_kwh,charge_yuan。",
     {"energy_kwh": 2400, "charge_yuan": 1344}, "medium"),
    ("saas-proration", "软件服务", "订阅每月300元，约定每月统一按30天计费。客户本月用了12天后取消，按未使用天数退款；不收手续费。只输出JSON，键used_days,unused_days,refund_yuan。",
     {"used_days": 12, "unused_days": 18, "refund_yuan": 180}, "low"),
    ("clinical-no-invention", "医疗数据", "仅整理记录，不作诊断。病历写“体温37.2℃，心率每分钟82次”，未提供血压。只输出JSON，键temperature_c,heart_rate,blood_pressure；缺失值必须为null，不得推测。",
     {"temperature_c": 37.2, "heart_rate": 82, "blood_pressure": None}, "medium"),
    ("education-weight", "教育", "课程总评：平时成绩70分权重20%，项目成绩85分权重30%，期末成绩90分权重50%。及格线60。只输出JSON，键weighted_score,passed。",
     {"weighted_score": 84.5, "passed": True}, "low"),
    ("hotel-occupancy", "酒店", "酒店总房100间，其中8间维修不开放；可售房中69间已售。入住率分母为可售房。只输出JSON，键sellable_rooms,sold_rooms,occupancy_percent。",
     {"sellable_rooms": 92, "sold_rooms": 69, "occupancy_percent": 75}, "high"),
    ("procurement-currency", "采购", "采购报价：40件，每件75美元，运费总计60美元。固定汇率1美元=7.2元人民币。不考虑税费。只输出JSON，键goods_usd,total_usd,total_cny。",
     {"goods_usd": 3000, "total_usd": 3060, "total_cny": 22032}, "medium"),
    ("construction-retention", "建筑", "工程已验收金额180000元，合同按验收金额扣5%质保金，此前已支付60000元。本次应付为验收金额减质保金再减已付。只输出JSON，键retention_yuan,payable_yuan。",
     {"retention_yuan": 9000, "payable_yuan": 111000}, "high"),
    ("retail-return", "零售", "订单买了3件商品，每件80元；整单折扣24元，平均分摊至每件。退1件，仅退该件实付金额。只输出JSON，键discount_per_item,refund_yuan。",
     {"discount_per_item": 8, "refund_yuan": 72}, "low"),
    ("telecom-overage", "电信", "套餐月费99元，包含20GB流量。超出部分每GB 5元，按实际用量不取整。用量23.5GB。只输出JSON，键overage_gb,overage_yuan,total_yuan。",
     {"overage_gb": 3.5, "overage_yuan": 17.5, "total_yuan": 116.5}, "medium"),
    ("transport-capacity", "交通运输", "每辆车最多装18箱，共有125箱待运，箱子不能拆分。只输出JSON，键minimum_vehicles,last_vehicle_boxes。",
     {"minimum_vehicles": 7, "last_vehicle_boxes": 17}, "low"),
    ("b2b-followup", "多轮业务", "改为本次只发120件，其他单价和折扣规则不变。只输出JSON，键quantity,subtotal,discount,total，金额为元。",
     {"quantity": 120, "subtotal": 6000, "discount": 600, "total": 5400}, "high"),
    ("it-incident-budget", "运维", "本月服务总观察时间43200分钟，可用性目标99.9%，允许停机时间=总时间×0.1%。已停机18分钟，本次事故又停机30分钟。只输出JSON，键allowed_minutes,total_downtime_minutes,exceeded_minutes。",
     {"allowed_minutes": 43.2, "total_downtime_minutes": 48, "exceeded_minutes": 4.8}, "high"),
]
for cid, family, text, expected, tier in new:
    case = {
        "id": "fresh-" + cid, "family": family, "title": family + "独立业务核算",
        "task": {"text": text, "routing_preferences": {"cost_tier": tier}},
        "allowed_workflows": ["general_text", "reasoning", "customer_support", "structured_output"],
        "judge": {"kind": "json", "expected": expected,
                  "rubric": "精确核对全部业务值，遵守无围栏JSON要求；不得补造未给定条件。"},
        "split": "frozen_holdout",
    }
    if cid == "b2b-followup":
        case["history"] = [
            {"role": "user", "text": "单价50元，满100件整单打九折；原计划买200件。"},
            {"role": "assistant", "text": "原计划200件，折后9000元。"},
        ]
    suite["cases"].append(case)
suite["parent_suite_version"] = suite.pop("version")
suite["evaluation_limits"] = [
    "52 previously seen tasks plus four local Agent turns are development regression.",
    "16 fresh business tasks frozen before any adapter holdout evaluation; labels never enter routing.",
    "Training uses synthetic prices and public-score utilities, not these business answers.",
    "This is a purposive, small evaluation; not coverage of every industry or a universal accuracy estimate.",
    "Manual judgments are unblinded; code requires execution where supplied; incomplete outputs remain failures.",
    "Local native Agent adapter tests do not replace real AgentCore V2 production checks.",
]
suite["version"] = hashlib.sha256(json.dumps(suite, sort_keys=True).encode()).hexdigest()[:24]
path = root / "suite.json"
assert not path.exists(), "Frozen suite already exists"
path.write_text(json.dumps(suite, ensure_ascii=False, indent=2))
print(suite["version"], len(suite["cases"]), "base tasks plus four Agent turns")
