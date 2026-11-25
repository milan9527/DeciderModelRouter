import copy
import json
import time
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException
from moto import mock_aws
from test_gateway_persistence import body
from test_gateway_persistence import client as client

from app import gateway_main, routing
from app.gateway_adapter import cache_usage
from app.gateway_store import Records
from app.routing_balance import observed_reference_cost


def events(client, payload):
    response = client.post("/api/gateway/runs", json=payload)
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines() if line]


def data(trace, stage):
    return next(e["data"] for e in trace if e.get("stage") == stage and e.get("status") == "completed")


def session(client):
    return client.post("/api/gateway/sessions", json={"title": "多轮测试"}).json()["id"]


def test_history_reaches_target_and_classifier_with_sticky_configuration(client):
    sid = session(client)
    first = events(client, body(sid))
    second = events(client, body(sid, task={
        **body()["task"], "text": "请把刚才那句话缩短",
    }))
    assert second[-1]["data"]["status"] == "executed"
    messages = gateway_main.app.state.bedrock.converse.call_args.kwargs["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"][0]["text"] == "重复请求只处理一次。"
    assert messages[-1]["content"][0]["text"] == "请把刚才那句话缩短"
    profile_input = json.loads(data(second, "classification")["model_input"]["state"])
    assert profile_input["recent_conversation"][1]["role"] == "assistant"
    assert data(second, "selection")["selection"]["stickiness"]["retained"] is True
    assert data(first, "selection")["choice"] == data(second, "selection")["choice"]
    stats = client.get("/api/gateway/performance").json()
    assert stats["context_requests"] == stats["sticky_requests"] == 1


def test_new_session_and_history_off_reset_context(client):
    sid = session(client)
    events(client, body(sid))
    reset = events(client, body(sid, session={"use_history": False}))
    assert next(e["data"] for e in reset if e["type"] == "context")["history_turns"] == 0
    assert len(gateway_main.app.state.bedrock.converse.call_args.kwargs["messages"]) == 1
    events(client, body(session(client)))
    assert len(gateway_main.app.state.bedrock.converse.call_args.kwargs["messages"]) == 1


def test_failed_and_analysis_runs_do_not_enter_context(client):
    sid = session(client)
    events(client, body(sid))
    events(client, body(sid, execute_target=False))
    bedrock = gateway_main.app.state.bedrock
    bedrock.converse.side_effect = ClientError({"Error": {"Code": "ValidationException"}}, "Converse")
    assert events(client, body(sid))[-1]["type"] == "error"
    bedrock.converse.side_effect = None
    final = events(client, body(sid))
    assert next(e["data"] for e in final if e["type"] == "context")["history_turns"] == 1


def test_explicit_history_and_trimming(client):
    history = [{"role": role, "text": text} for role, text in (
        ("user", "旧问题"), ("assistant", "旧答案"), ("user", "保留问题"), ("assistant", "保留答案"))]
    bad = client.post("/api/gateway/runs", json=body(history=history))
    assert bad.status_code == 422
    trace = events(client, body(history=history, session={"use_history": False, "max_history_turns": 1}))
    context = next(e["data"] for e in trace if e["type"] == "context")
    assert context["history_turns"] == context["trimmed_turns"] == 1
    assert gateway_main.app.state.bedrock.converse.call_args.kwargs["messages"][0]["content"][0]["text"] == "保留问题"
    invalid = body(history=[{"role": "assistant", "text": "wrong role"}], session={"use_history": False})
    assert client.post("/api/gateway/runs", json=invalid).status_code == 422


def test_cache_prefix_stays_stable_and_usage_is_not_discount_guess(client):
    sid = session(client)
    bedrock = gateway_main.app.state.bedrock
    bedrock.converse.return_value["usage"] = {
        "inputTokens": 10, "outputTokens": 12, "cacheReadInputTokens": 2048, "cacheWriteInputTokens": 1024,
    }
    trace = events(client, body(sid))
    first_message = copy.deepcopy(bedrock.converse.call_args.kwargs["messages"][0])
    events(client, body(sid))
    assert first_message == bedrock.converse.call_args.kwargs["messages"][0]
    previous_prefix = copy.deepcopy(bedrock.converse.call_args.kwargs["messages"][:-1])
    events(client, body(sid))
    assert bedrock.converse.call_args.kwargs["messages"][:len(previous_prefix)] == previous_prefix
    result = data(trace, "execution")
    assert result["cache"]["total_input_tokens"] == 3082
    assert result["cache"]["read_tokens"] == 2048
    assert result["observed_reference_cost_usd"] is None
    assert client.get("/api/gateway/performance").json()["cache_hit_requests"] == 3
    assert cache_usage({"inputTokens": 3}, {})["observed"] is False
    assert observed_reference_cost({"metrics": {"input_price": 1, "output_price": 1}},
                                   {"inputTokens": 10, "outputTokens": 2, "cacheWriteInputTokens": 1},
                                   "general_text") is None


def stream_events(*, partial_error=False):
    yield {"contentBlockDelta": {"delta": {"text": "真实"}}}
    if partial_error:
        yield {"throttlingException": {"message": "Busy after partial output"}}
        return
    yield {"contentBlockDelta": {"delta": {"text": "流式输出"}}}
    yield {"messageStop": {"stopReason": "end_turn"}}
    yield {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 6, "cacheReadInputTokens": 1024}}}


def test_streaming_preserves_deltas_final_usage_and_history(client):
    stream = MagicMock()
    stream.__iter__.return_value = stream_events()
    gateway_main.app.state.bedrock.converse_stream.return_value = {"stream": stream}
    trace = events(client, body(session={"stream": True}))
    assert "".join(e["data"]["text"] for e in trace if e["type"] == "delta") == "真实流式输出"
    output = data(trace, "execution")
    assert output["text"] == "真实流式输出" and output["complete"]
    assert output["timings"]["ttft_ms"] >= 0 and output["cache"]["read_tokens"] == 1024
    stream.close.assert_called()
    record = client.get("/api/gateway/history/" + trace[0]["request_id"]).json()
    assert record["conversation"][-1]["text"] == "真实流式输出"
    assert record["events"][-1]["type"] == "completed"


def fallback_body(**options):
    payload = body(session=options)
    payload["task"]["routing_preferences"]["allowed_models"].append("google.gemma-3-12b-it")
    return payload


def test_safe_throttle_fallback_and_endpoint_cooldown(client):
    bedrock = gateway_main.app.state.bedrock
    normal = bedrock.converse.return_value
    bedrock.converse.side_effect = [
        ClientError({"Error": {"Code": "ThrottlingException", "Message": "busy"}}, "Converse"),
        normal,
    ]
    trace = events(client, fallback_body())
    assert trace[-1]["data"]["status"] == "executed", trace[-1]
    output = data(trace, "execution")
    assert output["fallback_count"] == 1 and output["model_id"] == "google.gemma-3-12b-it"
    assert len(output["attempts"]) == 2 and bedrock.converse.call_count == 2
    bedrock.converse.side_effect = None
    retry = events(client, fallback_body())
    assert data(retry, "selection")["selected_model"]["id"] == "google.gemma-3-12b-it"
    assert client.get("/api/gateway/performance").json()["fallback_requests"] == 1


def test_partial_stream_must_never_fallback(client):
    stream = MagicMock()
    stream.__iter__.return_value = stream_events(partial_error=True)
    bedrock = gateway_main.app.state.bedrock
    bedrock.converse_stream.return_value = {"stream": stream}
    trace = events(client, fallback_body(stream=True))
    assert trace[-1]["type"] == "error"
    assert "".join(e["data"]["text"] for e in trace if e["type"] == "delta") == "真实"
    assert bedrock.converse_stream.call_count == 1
    failed = [e["data"] for e in trace if e["type"] == "attempt" and e["data"]["status"] == "failed"]
    assert failed[0]["partial_output"] and not failed[0]["will_fallback"]


def test_sticky_does_not_override_exclusion_or_budget(client):
    sid = session(client)
    events(client, body(sid))
    payload = body(sid)
    payload["task"]["routing_preferences"]["excluded_models"] = ["amazon.nova-micro-v1:0"]
    trace = events(client, payload)
    assert trace[-1]["data"]["status"] == "no_route"
    assert data(trace, "selection")["selection"]["stickiness"]["retained"] is False
    payload = body(sid)
    payload["task"]["routing_preferences"]["max_reference_cost_usd"] = "0"
    assert events(client, payload)[-1]["data"]["status"] == "no_route"


def test_gateway_receipt_cannot_be_reused_outside_locked_request(client):
    trace = events(client, body())
    token = data(trace, "selection")["route_token"]
    # The independent gateway does not expose /routing; test the bound receipt directly
    # through the shared handler with an authenticated request lacking its runtime.
    import asyncio
    from types import SimpleNamespace

    request = SimpleNamespace(app=gateway_main.app, state=SimpleNamespace(user={"username": "tester"}))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(routing.auto_execute(routing.AutoExecute(route_token=token), request))
    assert caught.value.status_code == 409


@pytest.mark.parametrize("backend", ["sqlite", "dynamodb"])
def test_session_lease_is_atomic_owner_checked_and_expires(tmp_path, backend):
    with mock_aws():
        if backend == "dynamodb":
            db = boto3.client("dynamodb", region_name="us-east-1")
            db.create_table(TableName="leases", BillingMode="PAY_PER_REQUEST",
                            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                            AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in ("pk", "sk")])
            store = Records(table="leases")
        else:
            store = Records(path=tmp_path / "lease.sqlite")
        store.acquire_lease("USER#a", "LEASE#one", "owner1")
        with pytest.raises(HTTPException) as caught:
            store.acquire_lease("USER#a", "LEASE#one", "owner2")
        assert caught.value.status_code == 409
        store.release_lease("USER#a", "LEASE#one", "wrong")
        assert store.get("USER#a", "LEASE#one")["owner"] == "owner1"
        store.acquire_lease("USER#b", "LEASE#one", "owner2")  # isolated namespace
        store.release_lease("USER#a", "LEASE#one", "owner1")
        assert store.get("USER#a", "LEASE#one") is None
        store.put("USER#a", "LEASE#one", {"owner": "stale", "expires_at": int(time.time()) - 1})
        store.acquire_lease("USER#a", "LEASE#one", "owner2")
        assert store.get("USER#a", "LEASE#one")["owner"] == "owner2"
