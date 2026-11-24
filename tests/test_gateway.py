import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app import gateway, routing
from app.gateway_decision import GatewayDecisionService
from app.main import app
from app.routing_profile import replay_profile
from app.routing_selector import SELECTOR_VERSION


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPEN_MODELS_URL", "http://private-gpu.test:18124")
    monkeypatch.setenv("OPEN_MODELS_API_KEY", "test-private-service-token")
    monkeypatch.setenv("GATEWAY_DECISION_PROVIDER", "h2o")
    monkeypatch.delenv("BEDROCK_ENABLED", raising=False)
    with TestClient(app, headers={"X-Decider-Client": "web"}) as client:
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "tester", "password": "test-password-with-entropy"},
            ).status_code
            == 200
        )
        yield client


def body(**changes):
    return {
        "task": {"text": "编写 Python 支付 webhook"},
        "catalog_version": routing.load_catalog()["version"],
        "ranking_version": routing.load_rankings()["version"],
        "policy_version": SELECTOR_VERSION,
        **changes,
    }


def gpu_reply():
    profile = replay_profile(routing.Task(text="编写 Python"), "coding")
    return {
        "model": routing.LOCAL_ROUTERS["h2o"],
        "answers": {k: {"choice": v} for k, v in profile.model_dump().items()},
        "usage": {"input_tokens": 100, "output_tokens": 7},
        "latency_ms": 11.5,
    }


def gpu_mock(client, handler=None):
    calls = []

    async def handle(request):
        calls.append(request)
        if handler:
            return handler(request)
        return httpx.Response(200, json=gpu_reply())

    service = app.state.gateway_decision
    service.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return calls


def events(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    result = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert [e["sequence"] for e in result] == list(range(1, len(result) + 1))
    assert len({e["request_id"] for e in result}) == 1
    assert [e["elapsed_ms"] for e in result] == sorted(e["elapsed_ms"] for e in result)
    return result


def test_code_preset_exposes_reasoning_budget_without_changing_api_defaults(client):
    from app.routing_balance import RoutingPreferences

    examples = client.get("/api/gateway/catalog").json()["examples"]
    code = next(row for row in examples if row["id"] == "code")
    assert code["routing_preferences"] == {
        "expected_output_tokens": 8192,
        "max_output_tokens": 16384,
    }
    assert RoutingPreferences(**code["routing_preferences"]).max_output_tokens == 16384
    assert RoutingPreferences().max_output_tokens == 4096
    assert RoutingPreferences().expected_output_tokens == 512


def test_private_gpu_called_once_and_each_phase_observed(client, monkeypatch):
    old = AsyncMock(side_effect=AssertionError("Gateway must use standalone GPU HTTP transport"))
    monkeypatch.setattr(routing, "infer_profile", old)
    calls = gpu_mock(client)
    app.state.bedrock = MagicMock()
    app.state.bedrock.converse.return_value = {
        "output": {"message": {"content": [{"text": "完成"}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 12, "outputTokens": 8},
    }
    result = events(client.post("/api/gateway/runs", json=body(execute_target=True)))
    assert len(calls) == 1
    assert str(calls[0].url) == "http://private-gpu.test:18124/v1/systemone/h2o"
    assert calls[0].headers["authorization"] == "Bearer test-private-service-token"
    assert len(json.loads(calls[0].content)["questions"]) == 7
    assert result[-1]["type"] == "completed"
    assert result[-1]["data"]["status"] == "executed"
    assert [(e["stage"], e["status"]) for e in result if e["type"] == "stage"] == [
        ("catalog", "completed"),
        ("classification", "started"),
        ("classification", "completed"),
        ("selection", "started"),
        ("selection", "completed"),
        ("execution", "started"),
        ("execution", "completed"),
    ]
    assessment = result[3]["data"]
    assert assessment["timings"]["service_reported_ms"] == 11.5
    assert assessment["timings"]["upstream_ms"] >= 0
    selected = next(
        e["data"] for e in result if e.get("stage") == "selection" and e["status"] == "completed"
    )
    assert selected["timings"]["policy_ms"] >= 0 and selected["timings"]["receipt_ms"] >= 0
    assert (
        selected["selected_model"]["invoke_id"]
        == app.state.bedrock.converse.call_args.kwargs["modelId"]
    )
    assert app.state.bedrock.converse.call_count == 1
    assert "test-private-service-token" not in json.dumps(result)
    old.assert_not_called()


def test_bedrock_throttle_has_actionable_error_without_retry_or_model_switch(client):
    calls = gpu_mock(client)
    app.state.bedrock = MagicMock()
    app.state.bedrock.converse.side_effect = ClientError(
        {
            "Error": {"Code": "ThrottlingException", "Message": "Too many tokens"},
            "ResponseMetadata": {"RequestId": "bedrock-request-test"},
        },
        "Converse",
    )
    result = events(client.post("/api/gateway/runs", json=body(execute_target=True)))
    assert len(calls) == app.state.bedrock.converse.call_count == 1
    assert result[-1]["type"] == "error"
    assert result[-1]["stage"] == "execution"
    assert result[-1]["data"]["code"] == 429
    assert result[-1]["data"]["retry_after_seconds"] == 60
    assert "Token 或请求配额不足" in result[-1]["data"]["message"]
    assert any(e["type"] == "route" for e in result)
    assert not any(e["type"] == "completed" for e in result)


def test_catalog_and_health_hide_private_urls_and_credentials(client):
    gpu_mock(
        client,
        lambda req: httpx.Response(
            200, json={"status": "starting", "models": {"h2o": False, "liquid": True}}
        ),
    )
    data = client.get("/api/gateway/catalog")
    assert data.status_code == 200
    assert data.json()["decision_service"]["default_provider"] == "h2o"
    assert "private-gpu.test" not in data.text and "test-private-service-token" not in data.text
    health = client.get("/api/gateway/status").json()
    assert health["decision"]["connected"] is False
    assert client.get("/api/gateway/status?provider=liquid").json()["decision"]["connected"] is True


def test_existing_vllm_model_identity_is_supported(monkeypatch):
    monkeypatch.setenv("GPU_PROVIDER", "vllm")
    monkeypatch.delenv("GATEWAY_DECIDER_MODEL", raising=False)
    service = GatewayDecisionService(MagicMock())
    assert service.services["decider"]["model"] == "strands-decider-2B-hobson-v19-vllm"
    monkeypatch.setenv("GATEWAY_DECIDER_MODEL", "custom-service-identity")
    assert (
        GatewayDecisionService(MagicMock()).services["decider"]["model"]
        == "custom-service-identity"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "bedrock"},
        {"upstream": "http://caller-url"},
        {"model_id": "caller-id"},
        {"mode": "replay", "execute_target": True},
    ],
)
def test_rejects_non_gpu_provider_url_model_overrides_and_replay_execution(client, change):
    calls = gpu_mock(client)
    assert client.post("/api/gateway/runs", json=body(**change)).status_code == 422
    assert not calls


@pytest.mark.parametrize("field", ["catalog_version", "ranking_version", "policy_version"])
def test_stale_versions_rejected_before_gpu_call(client, field):
    calls = gpu_mock(client)
    assert client.post("/api/gateway/runs", json=body(**{field: "stale"})).status_code == 409
    assert not calls


def test_replay_has_no_inference_or_performance_and_cannot_execute(client):
    calls = gpu_mock(client)
    result = events(client.post("/api/gateway/runs", json=body(mode="replay")))
    assert not calls
    route = next(e["data"] for e in result if e["type"] == "route")
    assert route["route_total_ms"] is None
    assert all(step["timings"] is None for step in route["steps"])
    assert route["steps"][1]["route_token"] is None
    assert result[-1]["data"]["backend_total_ms"] is None


@pytest.mark.parametrize(
    "failure,code",
    [("offline", 503), ("timeout", 504), ("identity", 502), ("profile", 502), ("usage", 502)],
)
def test_failed_gpu_never_selects_executes_or_silently_replays(client, failure, code):
    def handle(request):
        if failure == "offline":
            return httpx.Response(503)
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        data = gpu_reply()
        if failure == "identity":
            data["model"] = "wrong"
        if failure == "profile":
            del data["answers"]["difficulty"]
        if failure == "usage":
            data["usage"]["output_tokens"] = -1
        return httpx.Response(200, json=data)

    calls = gpu_mock(client, handle)
    result = events(client.post("/api/gateway/runs", json=body()))
    assert len(calls) == 1
    assert result[-1]["type"] == "error" and result[-1]["data"]["code"] == code
    assert result[-1]["stage"] == "classification"
    assert not any(e["type"] == "route" for e in result)
    assert app.state.gateway_decision.slots["h2o"]._value == 2


def test_no_route_does_not_execute_even_when_requested(client):
    calls = gpu_mock(client)
    app.state.bedrock = MagicMock()
    result = events(
        client.post(
            "/api/gateway/runs",
            json=body(
                task={"text": "x", "routing_preferences": {"min_success_rate": 0.99}},
                execute_target=True,
            ),
        )
    )
    assert len(calls) == 1
    assert result[-1]["data"]["status"] == "no_route"
    app.state.bedrock.converse.assert_not_called()


def test_partial_target_and_target_error_preserve_completed_routing(client):
    gpu_mock(client)
    app.state.bedrock = MagicMock()
    app.state.bedrock.converse.return_value = {
        "output": {"message": {"content": [{"text": "partial"}]}},
        "stopReason": "max_tokens",
        "usage": {"inputTokens": 12, "outputTokens": 8},
    }
    result = events(client.post("/api/gateway/runs", json=body(execute_target=True)))
    assert result[-1]["data"]["status"] == "incomplete"
    app.state.bedrock.converse.return_value = {}
    result = events(client.post("/api/gateway/runs", json=body(execute_target=True)))
    assert result[-1]["type"] == "error" and result[-1]["stage"] == "execution"
    assert any(e["type"] == "route" for e in result)


def test_auth_and_csrf_protect_stream(client):
    calls = gpu_mock(client)
    assert (
        client.post("/api/gateway/runs", json=body(), headers={"X-Decider-Client": ""}).status_code
        == 403
    )
    client.cookies.clear()
    assert client.post("/api/gateway/runs", json=body()).status_code == 401
    assert not calls


def test_disconnect_cancels_owned_worker(monkeypatch):
    async def check():
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def wait_forever(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(routing, "run_classified_route", wait_forever)
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(gateway_decision=SimpleNamespace(infer=AsyncMock()))
            )
        )
        stream = gateway.event_stream(gateway.GatewayRun(**body(provider="h2o")), request)
        first = json.loads(await anext(stream))
        assert first["type"] == "accepted"
        await asyncio.wait_for(entered.wait(), 1)
        await stream.aclose()
        assert cancelled.is_set()

    asyncio.run(check())
