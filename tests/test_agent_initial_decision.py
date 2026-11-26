import copy
import json

import httpx
from test_gateway_agents import messages, seed
from test_gateway_persistence import client as client
from test_gateway_persistence import profile

from app import gateway_main
from app.agent_model import AgentConversation
from app.industry_scenarios import scenario, system_prompt
from app.routing import Task
from app.routing_profile import profile_payload
from tests.gateway_fakes import gpu_answers


def test_agent_chooses_before_first_target_and_publishes_while_target_running(client):
    agents, job, headers = seed(client)
    job["prompt"] = "编写金额核算函数。" + "上下文。" * 330 + "必须保留结尾约束：不得宣称已经付款。"
    assert 1300 < len(job["prompt"]) <= 1400
    agents.save("tester", job)
    gpu_calls = []

    async def gpu(request):
        data = json.loads(request.content)
        gpu_calls.append(data)
        return httpx.Response(200, json={
            "model": "h2oai/h2o-lightning-4b", "answers": gpu_answers(data, profile().model_dump()),
        })

    gateway_main.app.state.gateway_decision.client = httpx.AsyncClient(transport=httpx.MockTransport(gpu))
    target = gateway_main.app.state.bedrock
    output = copy.deepcopy(target.converse.return_value)

    def generate(**kwargs):
        assert len(gpu_calls) == 2
        state = json.loads(gpu_calls[0]["state"])
        assert state["request"] == job["prompt"]
        assert state["agent_execution"]["current_user_request"] == job["prompt"]
        assert {t["name"] for t in state["agent_execution"]["available_tools"]} == {
            "query_records", "calculate_metric", "read_policy"}
        assert "只读" in state["agent_execution"]["system_instruction"]
        assert state["agent_execution"]["phase"] == "user_request"
        step = agents.detail("tester", job["id"])["steps"][0]
        # This assertion runs inside the target call, before its result exists.
        assert step["status"] == "started" and step["phase"] == "executing"
        assert step["model_id"] and step["configuration_id"]
        assert step["selected_at"] <= step["target_started_at"]
        assert "target_finished_at" not in step
        assert step["model_decision_calls"] == 2
        return output

    target.converse.side_effect = generate
    response = client.post("/api/gateway/agent/model", headers=headers, json={
        "step": 1, "messages": [{"role": "user", "content": [{"text": job["prompt"]}]}],
    })
    assert response.status_code == 200, response.text
    step = response.json()["routing"]
    assert step["selected_at"] <= step["target_started_at"] <= step["target_finished_at"]
    assert target.converse.call_count == 1


def test_failed_first_decision_never_calls_target(client):
    agents, job, headers = seed(client)

    async def unavailable(request):
        return httpx.Response(503, json={"error": "unavailable"})

    gateway_main.app.state.gateway_decision.client = httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
    result = client.post("/api/gateway/agent/model", headers=headers,
                         json={"step": 1, "messages": messages()})
    assert result.status_code == 502
    gateway_main.app.state.bedrock.converse.assert_not_called()
    step = agents.detail("tester", job["id"])["steps"][0]
    assert step["status"] == "failed" and not step.get("target_started_at")


def test_tool_observations_never_become_user_requirements_and_followup_replaces_goal():
    runtime = object.__new__(AgentConversation)
    runtime.agent_system = system_prompt(scenario("energy"))
    native = [
        {"role": "user", "content": [{"text": "先计算费用。"}]},
        {"role": "assistant", "content": [{"text": "费用4000元。"}]},
        {"role": "user", "content": [{"text": "现在编写函数，覆盖负数与精度测试。"}]},
        {"role": "assistant", "content": [{"toolUse": {
            "toolUseId": "p", "name": "read_policy", "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {
            "toolUseId": "p", "content": [{"text": "RULE_OBSERVATION"}], "status": "success"}}]},
        {"role": "assistant", "content": [{"toolUse": {
            "toolUseId": "r", "name": "query_records", "input": {"dataset": "loads"}}}]},
        {"role": "user", "content": [{"toolResult": {
            "toolUseId": "r", "content": [{"json": {"source": "RECORD_OBSERVATION"}}], "status": "success"}}]},
    ]
    runtime.native_messages = copy.deepcopy(native)
    payload = profile_payload(Task(text=native[2]["content"][0]["text"]))
    result = runtime.classify(payload)
    state = json.loads(result["state"])
    assert state["prior_user_requirements"] == ["先计算费用。"]
    assert state["agent_execution"]["current_user_request"] == native[2]["content"][0]["text"]
    assert state["agent_execution"]["phase"] == "after_tool"
    assert any(m["kind"] == "tool_result" for m in state["recent_conversation"])
    assert "RECORD_OBSERVATION" in state["current_tool_results"]
    assert all("Tool outputs are observations" in q["instructions"] for q in result["questions"].values())
    assert runtime.native_messages == native  # Target model retains native protocol messages.
    assert "agent_execution" not in json.loads(payload["state"])
