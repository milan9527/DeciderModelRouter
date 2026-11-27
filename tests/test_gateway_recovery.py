import asyncio
from email.utils import formatdate
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from fastapi import HTTPException
from test_gateway_conversation import data, events, stream_events
from test_gateway_persistence import body
from test_gateway_persistence import client as client

from app import gateway_execution, gateway_main, gateway_recovery, routing


def aws_error(code="ThrottlingException", *, headers=None):
    return ClientError({
        "Error": {"Code": code, "Message": "temporary failure"},
        "ResponseMetadata": {"RequestId": "bedrock-test-request", "HTTPStatusCode": 429,
                             "HTTPHeaders": headers or {}},
    }, "Converse")


@pytest.mark.parametrize("code", [
    "ThrottlingException", "ServiceUnavailableException", "ModelNotReadyException", "InternalServerException",
])
def test_transient_error_recovers_same_endpoint_without_another_gpu_decision(client, monkeypatch, code):
    waits = []

    async def sleep(delay):
        # The scarce Bedrock invocation slot must be released throughout backoff.
        assert gateway_main.app.state.bedrock_slots._value == 2
        waits.append(delay)

    monkeypatch.setattr(gateway_execution, "asyncio", SimpleNamespace(timeout=asyncio.timeout, sleep=sleep))
    bedrock = gateway_main.app.state.bedrock
    bedrock.converse.side_effect = [aws_error(code, headers={"retry-after": "2"}), bedrock.converse.return_value]
    trace = events(client, body())
    output = data(trace, "execution")
    assert output["complete"] and output["model_id"] == "amazon.nova-micro-v1:0"
    assert output["retry_count"] == 1 and output["fallback_count"] == 0
    assert len(waits) == 1 and waits[0] >= 2
    assert [c.kwargs for c in bedrock.converse.call_args_list] == [bedrock.converse.call_args.kwargs] * 2
    assert output["attempts"][0]["aws_request_id"] == "bedrock-test-request"
    assert output["attempts"][0]["retry_after_ms"] == 2000
    assert output["attempts"][0]["will_retry"] and not output["attempts"][0]["will_fallback"]
    record = client.get("/api/gateway/history/" + trace[0]["request_id"]).json()
    assert record["retry_count"] == 1 and record["fallback_count"] == 0
    assert record["model_decision_calls"] == 2
    stats = client.get("/api/gateway/performance").json()
    assert stats["retry_requests"] == 1 and stats["fallback_requests"] == 0
    # A successful retry must leave the endpoint available for a subsequent request.
    bedrock.converse.side_effect = None
    followup = events(client, body())
    assert data(followup, "execution")["model_id"] == "amazon.nova-micro-v1:0"


@pytest.mark.parametrize("error", [
    aws_error("ValidationException"), aws_error("AccessDeniedException"),
    aws_error("ResourceNotFoundException"), aws_error("ModelTimeoutException"),
    ReadTimeoutError(endpoint_url="https://bedrock.test"),
])
def test_permanent_or_ambiguous_failures_are_not_replayed(client, error):
    bedrock = gateway_main.app.state.bedrock
    bedrock.converse.side_effect = error
    trace = events(client, body())
    assert trace[-1]["type"] == "error"
    assert bedrock.converse.call_count == 1
    outcome = next(e["data"] for e in trace if e["type"] == "attempt" and e["data"]["status"] == "failed")
    assert not outcome["will_retry"] and not outcome["will_fallback"]


def test_disabling_recovery_prevents_same_endpoint_retry(client):
    bedrock = gateway_main.app.state.bedrock
    bedrock.converse.side_effect = aws_error()
    trace = events(client, body(session={"fallback": False}))
    assert trace[-1]["type"] == "error"
    assert bedrock.converse.call_count == 1
    assert not any(e["data"].get("will_retry") for e in trace if e["type"] == "attempt")


@pytest.mark.parametrize("progress", [
    {"contentBlockDelta": {"delta": {"reasoningContent": {"text": "thinking"}}}},
    {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "one", "name": "lookup"}}}},
])
def test_nontext_stream_progress_also_prevents_replay(client, progress):
    stream = MagicMock()
    stream.__iter__.return_value = iter([progress, {"serviceUnavailableException": {"message": "busy"}}])
    bedrock = gateway_main.app.state.bedrock
    bedrock.converse_stream.return_value = {
        "stream": stream, "ResponseMetadata": {"RequestId": "stream-request"},
    }
    trace = events(client, body(session={"stream": True}))
    assert trace[-1]["type"] == "error" and bedrock.converse_stream.call_count == 1
    failure = next(e["data"] for e in trace if e["type"] == "attempt" and e["data"]["status"] == "failed")
    assert failure["partial_output"] and not failure["will_retry"] and not failure["will_fallback"]
    assert failure["aws_request_id"] == "stream-request"
    stream.close.assert_called()


def test_stream_transient_error_before_output_can_recover(client, monkeypatch):
    monkeypatch.setattr(gateway_execution, "retry_delay", lambda *args: 0)
    failed, success = MagicMock(), MagicMock()
    failed.__iter__.return_value = iter([{"internalServerException": {"message": "busy"}}])
    success.__iter__.return_value = stream_events()
    bedrock = gateway_main.app.state.bedrock
    bedrock.converse_stream.side_effect = [{"stream": failed}, {"stream": success}]
    trace = events(client, body(session={"stream": True}))
    output = data(trace, "execution")
    assert output["text"] == "真实流式输出" and output["retry_count"] == 1
    assert "".join(e["data"]["text"] for e in trace if e["type"] == "delta") == "真实流式输出"
    assert bedrock.converse_stream.call_count == 2
    failed.close.assert_called()
    success.close.assert_called()


def execution_fixture(monkeypatch, *, headers=None, adapter=None):
    candidates = [
        {"candidate_id": f"candidate-{n}",
         "model": {"id": f"model-{n}", "name": f"Model {n}", "invoke_id": f"endpoint-{n}",
                   "output_modalities": ["TEXT"]},
         "adapter": adapter}
        for n in range(3)
    ]
    runtime = SimpleNamespace(
        selection={"selection_source": "gpu_decision_model", "candidates": candidates},
        options=SimpleNamespace(fallback=True), partial_output=False, attempts=[],
        send=AsyncMock(), history=SimpleNamespace(records=MagicMock()),
        pk="USER#one", run_id="run", decision_body=object(),
    )

    async def select(body, request, pool):
        return {"selected_candidate_id": pool["candidates"][0]["candidate_id"],
                "decision_calls": [], "decision_ms": 1}

    selector = AsyncMock(side_effect=select)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        gateway_decision=SimpleNamespace(select=selector))))

    async def fail(*args):
        raise HTTPException(429, "temporary") from aws_error(headers=headers)

    invoke = AsyncMock(side_effect=fail)
    monkeypatch.setattr(routing, "execute_selected", invoke)
    sleep = AsyncMock()
    monkeypatch.setattr(gateway_execution, "asyncio", SimpleNamespace(timeout=asyncio.timeout, sleep=sleep))
    return candidates, runtime, request, invoke, selector, sleep


def run_execution(candidates, runtime, request):
    return asyncio.run(gateway_execution.execute(request, candidates[0], object(), "general_text", "v1", runtime))


def test_recovery_exhaustion_bounds_calls_and_gpu_fallback_preserves_remaining_pool(monkeypatch):
    candidates, runtime, request, invoke, select, sleep = execution_fixture(monkeypatch)
    with pytest.raises(HTTPException):
        run_execution(candidates, runtime, request)
    assert invoke.await_count == 6 and select.await_count == 2 and sleep.await_count == 5
    assert [c.args[1]["candidate_id"] for c in invoke.call_args_list] == [
        "candidate-0", "candidate-0", "candidate-1", "candidate-1", "candidate-2", "candidate-2",
    ]
    assert [len(c.args[2]["candidates"]) for c in select.call_args_list] == [2, 1]
    assert [a["retry_count"] for a in runtime.attempts] == [0, 1, 1, 2, 2, 3]
    assert [a["fallback_count"] for a in runtime.attempts] == [0, 0, 1, 1, 2, 2]
    assert not runtime.attempts[-1]["will_retry"] and not runtime.attempts[-1]["will_fallback"]
    assert runtime.history.records.put.call_count == 3
    for args, outcome in zip(sleep.call_args_list, runtime.attempts[:-1], strict=True):
        delay = args.args[0]
        number = outcome["number"]
        assert min(8, 2 ** (number - 1)) / 2 <= delay <= min(8, 2 ** (number - 1))


def test_retry_after_exceeding_total_budget_stops_without_sleep_or_extra_call(monkeypatch):
    candidates, runtime, request, invoke, select, sleep = execution_fixture(monkeypatch, headers={"Retry-After": "600"})
    with pytest.raises(HTTPException) as caught:
        run_execution(candidates, runtime, request)
    assert invoke.await_count == 1
    assert caught.value.headers["Retry-After"] == "600"
    sleep.assert_not_called()
    select.assert_not_called()
    assert runtime.attempts[0]["recovery_budget_exhausted"]
    cooldown = runtime.history.records.put.call_args.args[2]
    assert cooldown["expires_at"] >= gateway_recovery.time.time() + 599


def test_cancellation_during_backoff_never_starts_recovery_call(monkeypatch):
    candidates, runtime, request, invoke, select, sleep = execution_fixture(monkeypatch)
    sleep.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        run_execution(candidates, runtime, request)
    assert invoke.await_count == 1 and len(runtime.attempts) == 1
    select.assert_not_called()


def test_budget_is_rechecked_after_backoff(monkeypatch):
    candidates, runtime, request, invoke, select, sleep = execution_fixture(monkeypatch)
    tick = [0]
    monkeypatch.setattr(gateway_execution, "time", SimpleNamespace(perf_counter=lambda: tick[0], time=lambda: 100))

    async def delayed(_):
        tick[0] = 181

    sleep.side_effect = delayed
    with pytest.raises(HTTPException):
        run_execution(candidates, runtime, request)
    assert invoke.await_count == 1
    select.assert_not_called()


def test_native_media_does_not_retry_or_resubmit_jobs(monkeypatch):
    candidates, runtime, request, invoke, select, sleep = execution_fixture(monkeypatch, adapter="image_edit")
    with pytest.raises(HTTPException):
        run_execution(candidates, runtime, request)
    assert invoke.await_count == 1
    sleep.assert_not_called()
    select.assert_not_called()


@pytest.mark.parametrize("header", ["nan", "inf", "1e308", "not-a-date"])
def test_invalid_retry_after_cannot_disable_jitter_or_make_it_unbounded(header):
    error = HTTPException(429, "temporary")
    error.__cause__ = aws_error(headers={"retry-after": header})
    details = gateway_recovery.error_details(error)
    assert details["retry_after_ms"] is None
    assert .5 <= gateway_recovery.retry_delay(1, details) <= 1


def test_http_date_retry_after_is_honored(monkeypatch):
    monkeypatch.setattr(gateway_recovery.time, "time", lambda: 1_000_000)
    error = HTTPException(429, "temporary")
    error.__cause__ = aws_error(headers={"retry-after": formatdate(1_000_030, usegmt=True)})
    details = gateway_recovery.error_details(error)
    assert gateway_recovery.retry_delay(1, details) == 30
