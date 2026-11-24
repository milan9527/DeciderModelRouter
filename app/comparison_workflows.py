"""Business workflows: model-selected actions, real sandbox state and separate acceptance."""

import copy
import json

from app import crypto, routing
from app.routing_policy import WORKFLOWS, candidate_pool, load_rankings, model_payload

GROUPS = [
    {"id": "payment", "name": "支付客服"},
    {"id": "routing", "name": "模型路由"},
    {"id": "device_control", "name": "设备控制"},
    {"id": "temperature_multiselect", "name": "常用温度"},
]
DEVICE_QUESTIONS = {
    "power": {
        "type": "choice", "instructions": "Choose the power change explicitly requested by the user.",
        "criteria": {"on": "Turn power on.", "off": "Turn power off.",
                     "unchanged": "The user requests no power change."},
    },
    "temperature": {
        "type": "choice",
        "instructions": "Choose the final integer setpoint, applying any requested relative change to current.temperature_c. Valid range is 16 through 30 Celsius.",
        "criteria": {
            **{str(t): f"The requested final setpoint is exactly {t} Celsius." for t in range(16, 31)},
            "unchanged": "No temperature change was requested.",
            "clarify": "The requested setpoint is outside 16–30, not an integer, or cannot be determined.",
        },
    },
    "mode": {
        "type": "choice", "instructions": "Choose the requested operating mode.",
        "criteria": {"cool": "Cooling is requested.", "heat": "Heating is requested.",
                     "dry": "Dehumidifying is requested.", "fan": "Fan-only is requested.",
                     "unchanged": "No mode change was requested."},
    },
}
DEVICE_LABELS = {"on": "开机", "off": "关机", "unchanged": "保持不变",
                 "clarify": "需要澄清", "cool": "制冷", "heat": "制热", "dry": "除湿", "fan": "送风"}


def case_catalog():
    cases = []
    for row in crypto.CASE_META:
        scenario = row["id"]
        s = crypto.initial_snapshot(row)
        cases.append({
            "id": f"payment-{scenario}", "group": "payment",
            "title": row["title"], "original": True, "input_source": "crypto.fixed.baseline",
            "task": row["message"], "goal": "原始快照五步判断，再执行建议并展示资金与工单状态",
            "steps": [{"id": r["id"], "title": r["label"], "kind": "decision"} for r in crypto.STAGES],
            "initial": s.model_dump(mode="json"),
        })
    for row in routing.CASES:
        cases.append({
            "id": "routing-" + row["id"], "group": "routing", "title": row["title"],
            "original": True, "input_source": "routing.original",
            "task": row["task"], "goal": "原始流程：选择处理流程 → 选择目标型号",
            "steps": [
                {"id": "workflow", "title": "识别处理流程", "kind": "decision"},
                {"id": "model", "title": "选择目标模型", "kind": "decision"},
            ],
            "initial": {"request": row["task"]},
        })
    for suffix, title, task in (
        ("comfort", "回家开启空调", "开机，设为24度，使用制冷模式。"),
        ("relative", "调整温度，保留其他设置", "把设定温度降低2度，其他不变。"),
    ):
        cases.append({
            "id": "device-" + suffix, "group": "device_control", "title": title,
            "task": task, "goal": "按用户要求设置电源、温度与模式",
            "steps": [{"id": k, "title": title, "kind": "decision"} for k, title in (
                ("power", "设置电源"), ("temperature", "设置温度"), ("mode", "设置模式"))],
            "initial": {"power": "off", "temperature_c": 26, "mode": "heat"},
        })
    cases.append({
        "id": "temperature-presets", "group": "temperature_multiselect", "title": "设置常用温度范围",
        "task": "把常用温度替换为22到25度之间的所有整数温度，包含22度和25度，删除旧的常用温度。",
        "goal": "常用温度恰好为 22、23、24、25°C，并确认设置完成",
        "steps": [
            {"id": "operation", "title": "识别设置方式", "kind": "decision"},
            {"id": "selection", "title": "选择并设置温度", "kind": "decision"},
            {"id": "confirm", "title": "读取结果并确认", "kind": "decision"},
        ], "initial": {"presets": [20, 26]},
    })
    return cases


CASES = {row["id"]: row for row in case_catalog()}


def question(state, instructions, criteria):
    return {"state": json.dumps(state, ensure_ascii=False), "questions": {
        "action": {"type": "choice", "instructions": instructions, "criteria": criteria},
    }}


class Workflow:
    def __init__(self, case, task):
        self.case, self.task = case, task
        self.state = copy.deepcopy(case["initial"])
        self.edited = task != case["task"]
        self.feedback = []
        self.selected_workflow = None
        self.selected_model = None
        self.pool = None
        self.target_result = None
        self.operation = None
        self.confirmed = False
        self.catalog = routing.load_catalog() if case["group"] == "routing" else None
        self.rankings = load_rankings() if self.catalog else None
        if case["group"] == "payment":
            self.state["message"] = task
        self.decision_snapshot = copy.deepcopy(self.state)

    def prepare(self, stage):
        group = self.case["group"]
        expected = None
        if group == "payment":
            snapshot = crypto.Snapshot(**self.decision_snapshot)
            payload = crypto.payload_for(snapshot, stage, input_profile="baseline")
            expected = {"action": crypto.reference(snapshot, stage)}
        elif group == "routing":
            task = routing.Task(text=self.task)
            if stage == "workflow":
                payload = routing.workflow_payload(task)
                expected = {"action": {"routing-support": "customer_support", "routing-code": "coding",
                                       "routing-analysis": "reasoning", "routing-retrieval": "embedding"}[self.case["id"]]}
            else:
                if self.selected_workflow is None:
                    raise ValueError("尚未获得有效的处理流程")
                self.pool = candidate_pool(self.catalog, self.rankings, task, self.selected_workflow, routing.exclusion)
                if not self.pool["candidates"] or len(self.pool["candidates"]) > 24:
                    raise ValueError("原始流程候选数量不在 1–24 范围；未替换流程或裁剪候选")
                payload = model_payload(task, self.selected_workflow, self.pool, self.rankings)
        elif group == "device_control":
            payload = {"state": json.dumps({"user_request": self.task, "current": self.state}, ensure_ascii=False),
                       "questions": {stage: copy.deepcopy(DEVICE_QUESTIONS[stage])}}
            choices = ({"power": "on", "temperature": "24", "mode": "cool"}
                       if self.case["id"] == "device-comfort"
                       else {"power": "unchanged", "temperature": "24", "mode": "unchanged"})
            expected = {stage: choices[stage]}
        else:
            state = {"user_request": self.task, "current_presets": self.state["presets"]}
            if stage == "operation":
                payload = question(state, "Choose how the user wants to update their saved temperature presets.", {
                    "replace": "Replace all old presets with the requested set.",
                    "add": "Add requested values while retaining all old presets.",
                    "clear": "Remove every preset and leave the set empty.",
                })
                expected = {"action": "replace"}
            elif stage == "selection":
                if self.operation is None:
                    raise ValueError("尚未获得有效的设置方式")
                payload = {"state": json.dumps(state, ensure_ascii=False), "questions": {
                    f"t{t}": {"type": "choice", "instructions": f"Does {t} Celsius belong to the temperature set requested by the user?",
                              "criteria": {"yes": f"The requested set includes {t}.",
                                           "no": f"The requested set excludes {t}."}} for t in range(16, 31)
                }}
                expected = {f"t{t}": "yes" if 22 <= t <= 25 else "no" for t in range(16, 31)}
            else:
                payload = question(state, "Read the actual saved presets. Decide whether the user's requested update is complete.", {
                    "complete": "The actual saved set exactly matches the request, with no missing or unwanted values.",
                    "needs_correction": "The actual saved set still contains a missing or unwanted value.",
                })
                expected = {"action": "complete" if self.state["presets"] == [22, 23, 24, 25] else "needs_correction"}
        return payload, None if self.edited else expected

    def apply(self, stage, choices):
        group = self.case["group"]
        if group == "payment":
            result = crypto.apply(crypto.Transition(snapshot=crypto.Snapshot(**self.state), stage=stage,
                                                   choice=choices["action"]))
            self.state = result["snapshot"]
            self.feedback.append(crypto.ExecutionFeedback(
                stage=stage, requested_action=choices["action"], actual_action=result["action"],
                status="protected" if result["safety_adjusted"] else "applied", reason=result["message"][:800],
            ))
            return {"label": result["requested_label"], "tool": next(r["tool"] for r in crypto.STAGES if r["id"] == stage),
                    "simulated": True, **result}
        if group == "routing":
            if stage == "workflow":
                self.selected_workflow = choices["action"]
                return {"label": WORKFLOWS[self.selected_workflow]["label"], "workflow": self.selected_workflow,
                        "message": "后续候选由该模型实际选择的流程确定。"}
            self.selected_model = next(r for r in self.pool["candidates"] if r["candidate_id"] == choices["action"])
            model = self.selected_model["model"]
            return {"label": model["name"], "model_id": model["id"], "benchmark": self.selected_model["benchmark"],
                    "message": "已选择目标型号，下一步调用这个型号执行任务。"}
        before = copy.deepcopy(self.state)
        if group == "device_control":
            value = choices[stage]
            if value not in {"unchanged", "clarify"}:
                self.state["temperature_c" if stage == "temperature" else stage] = int(value) if stage == "temperature" else value
            label = f"{value}°C" if stage == "temperature" and value.isdigit() else DEVICE_LABELS[value]
            return {"label": label, "tool": "simulator.device.set_" + stage, "simulated": True,
                    "before": before, "after": copy.deepcopy(self.state),
                    "message": "设备状态已更新。" if before != self.state else "设备状态保持不变。"}
        if stage == "operation":
            self.operation = choices["action"]
            label = {"replace": "替换原有温度", "add": "保留并新增", "clear": "清空温度"}[self.operation]
        elif stage == "selection":
            selected = [int(k[1:]) for k, v in choices.items() if v == "yes"]
            self.state["presets"] = (sorted(set(before["presets"] + selected)) if self.operation == "add"
                                     else [] if self.operation == "clear" else selected)
            label = "、".join(f"{t}°C" for t in selected) or "未选择温度"
        else:
            self.confirmed = choices["action"] == "complete"
            label = "确认完成" if self.confirmed else "仍需修改"
        return {"label": label, "tool": "simulator.presets." + stage, "simulated": True,
                "before": before, "after": copy.deepcopy(self.state), "message": label}

    def execute_target(self, client):
        if self.selected_model is None:
            raise ValueError("未选出目标模型，未执行下游任务")
        row = self.selected_model
        self.target_result = routing.execute_target(
            client, row["model"], routing.Task(text=self.task), self.selected_workflow, row["benchmark"],
        )
        return {"label": "目标任务已返回", "model_id": row["model"]["id"], "simulated": False, **self.target_result}

    def original_request(self, stage, provider):
        if self.case["group"] == "payment":
            return {"snapshot": copy.deepcopy(self.decision_snapshot), "stage": stage,
                    "provider": provider, "mode": "live", "input_profile": "baseline",
                    **({"model_id": "us.openai.gpt-6.1-sol"} if provider == "bedrock" else {})}
        return {"task": routing.Task(text=self.task).model_dump(), "stage": stage,
                "provider": provider, "mode": "live", "strategy": "original",
                "catalog_version": self.catalog["version"], "ranking_version": self.rankings["version"],
                **({"workflow": self.selected_workflow} if stage == "model" else {})}

    def target_request(self):
        if self.selected_model is None:
            return None
        return {"task": routing.Task(text=self.task).model_dump(), "workflow": self.selected_workflow,
                "model_id": self.selected_model["model"]["id"], "strategy": "original",
                "catalog_version": self.catalog["version"], "ranking_version": self.rankings["version"]}

    def finish(self):
        group = self.case["group"]
        if group == "payment":
            target = {"refund": "refunded", "settlement": "settled"}.get(self.state["scenario"], "paid")
            checks = [
                {"label": "模拟资金处理完成", "passed": self.state["order_status"] == target},
                {"label": "客服工单已结案", "passed": self.state["ticket_status"] == "resolved"},
            ]
            text = crypto.reply_text(crypto.Snapshot(**self.state))
        elif group == "routing":
            checks = [
                {"label": "已选择处理流程", "passed": self.selected_workflow is not None},
                {"label": "已选择目标型号", "passed": self.selected_model is not None},
            ]
            text = (f"{WORKFLOWS[self.selected_workflow]['label']} → {self.selected_model['model']['name']}。"
                    if self.selected_model else "路由选择尚未完成。")
        elif group == "device_control":
            target = ({"power": "on", "temperature_c": 24, "mode": "cool"} if self.case["id"] == "device-comfort"
                      else {"power": "off", "temperature_c": 24, "mode": "heat"})
            checks = [{"label": label, "passed": self.state[key] == value} for key, value, label in (
                ("power", target["power"], "电源符合要求"), ("temperature_c", target["temperature_c"], "温度符合要求"),
                ("mode", target["mode"], "模式符合要求"))]
            text = f"电源：{DEVICE_LABELS[self.state['power']]}；温度：{self.state['temperature_c']}°C；模式：{DEVICE_LABELS[self.state['mode']]}。"
        else:
            checks = [{"label": "保存的温度集合正确", "passed": self.state["presets"] == [22, 23, 24, 25]},
                      {"label": "模型确认设置完成", "passed": self.confirmed}]
            text = "当前常用温度：" + ("、".join(f"{t}°C" for t in self.state["presets"]) or "空集合") + "。"
        return {
            "complete": all(c["passed"] for c in checks) if not self.edited else None,
            "checks": checks if not self.edited else [], "state": copy.deepcopy(self.state), "text": text,
            "acceptance_note": ("自定义任务不沿用样例验收，请核对实际结果。" if self.edited else
                                "仅表示两步路由返回有效选择。型号适用性请核对候选证据；目标执行另行触发。" if group == "routing" else
                                "按实际执行后的业务状态验收，不用步骤数量代替完成结果。"),
        }
