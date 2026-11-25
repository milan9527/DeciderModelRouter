"""Container smoke: actual Strands loop, synthetic router, complete native transcript."""

import asyncio
import json
import os

import httpx

from agent_runtime import main as runtime
from app.industry_scenarios import catalog


async def main():
    original = httpx.AsyncClient
    requests = []
    fail_next = False

    async def callback(request):
        assert request.headers["accept"] == "application/x-ndjson"
        body = json.loads(request.content)
        requests.append(body)
        if fail_next:
            return httpx.Response(200, content='\n\n{"error":{"code":504,"message":"synthetic timeout"}}',
                                  headers={"Content-Type": "application/x-ndjson"})
        if body["step"] == 1:
            message = {"role": "assistant", "content": [{"toolUse": {
                "toolUseId": "lookup1", "name": "query_records", "input": {"dataset": "inventory"},
            }}]}
            stop = "tool_use"
        else:
            assert "toolResult" in body["messages"][-1]["content"][0]
            message = {"role": "assistant", "content": [{"text": "SKU A库存35件。"}]}
            stop = "end_turn"
        payload = {
            "message": message, "stop_reason": stop,
            "usage": {"inputTokens": 100, "outputTokens": 10, "totalTokens": 110},
            "routing": {"step": body["step"], "target_ms": 5, "model_id": "fixture-model"},
        }
        return httpx.Response(200, content="\n\n" + json.dumps(payload),
                              headers={"Content-Type": "application/x-ndjson"})

    os.environ["MODEL_ROUTER_URL"] = "https://synthetic-router.test"
    runtime.httpx.AsyncClient = lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(callback),
    )
    try:
        async with original(transport=httpx.ASGITransport(app=runtime.app), base_url="http://runtime") as client:
            response = await client.post("/invocations", json={
                "scenario_id": "retail", "dataset_version": catalog()["version"],
                "prompt": "查询库存", "messages": [],
                "callback_token": "synthetic-token-not-a-real-secret-1234", "max_model_calls": 6,
            })
            response.raise_for_status()
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            final = events[-1]
            assert final["type"] == "completed", final
            assert final["answer"] == "SKU A库存35件。"
            assert len(final["messages"]) == 4
            assert all(set(m) == {"role", "content"} for m in final["messages"])
            assert [m["role"] for m in final["messages"]] == ["user", "assistant", "user", "assistant"]
            assert final["messages"][2]["content"][0]["toolResult"]["toolUseId"] == "lookup1"
            assert any(e["type"] == "tool_result" for e in events)
            assert len(requests) == 2
            # A real loop that needs a second call must stop at a budget of one.
            limited = await client.post("/invocations", json={
                "scenario_id": "retail", "dataset_version": catalog()["version"],
                "prompt": "查询库存", "messages": [],
                "callback_token": "synthetic-token-not-a-real-secret-1234", "max_model_calls": 1,
            })
            last = [json.loads(line[6:]) for line in limited.text.splitlines() if line.startswith("data: ")][-1]
            assert last["type"] == "error" and len(requests) == 3
            fail_next = True
            failed = await client.post("/invocations", json={
                "scenario_id": "retail", "dataset_version": catalog()["version"],
                "prompt": "查询库存", "messages": [],
                "callback_token": "synthetic-token-not-a-real-secret-1234", "max_model_calls": 6,
            })
            last = [json.loads(line[6:]) for line in failed.text.splitlines() if line.startswith("data: ")][-1]
            assert last["type"] == "error" and "504" in last["message"] and len(requests) == 4
            print(json.dumps({"native_tools": True, "canonical_persisted_transcript": True,
                              "call_budget_enforced": True, "heartbeat_json": True,
                              "structured_error_without_retry": True, "real_aws_calls": 0}))
    finally:
        runtime.httpx.AsyncClient = original


if __name__ == "__main__":
    asyncio.run(main())
