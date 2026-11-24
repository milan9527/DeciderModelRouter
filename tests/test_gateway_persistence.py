import json
import sqlite3
import time
from unittest.mock import MagicMock

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from moto import mock_aws

from app import gateway_main, routing
from app.auth import save_user
from app.gateway_store import History, Records
from app.routing_profile import RequestProfile
from app.routing_selector import SELECTOR_VERSION

HEADERS = {"X-Decider-Client": "web"}
CREDS = {"username": "tester", "password": "test-password-with-entropy"}


def profile():
    return RequestProfile(
        workflow="general_text",
        difficulty="simple",
        stronger_model_benefit="low",
        latency="normal",
        tools="no",
        structured_output="no",
        languages="zh",
    )


@pytest.fixture
def client(tmp_path, monkeypatch, auth_config):
    monkeypatch.setenv("GATEWAY_DB_PATH", str(tmp_path / "history.sqlite3"))
    monkeypatch.delenv("GATEWAY_DATA_TABLE", raising=False)
    monkeypatch.setenv("OPEN_MODELS_URL", "http://private-gpu.test")
    monkeypatch.setenv("GATEWAY_DECISION_PROVIDER", "h2o")
    monkeypatch.setattr(gateway_main, "create_client", lambda **kw: None)
    user = auth_config.get("USER#tester")
    user["role"] = "admin"
    auth_config.put(user)
    with TestClient(gateway_main.app, base_url="https://testserver", headers=HEADERS) as c:
        assert c.post("/api/auth/login", json=CREDS).status_code == 200

        async def gpu(request):
            return httpx.Response(
                200,
                json={
                    "model": routing.LOCAL_ROUTERS["h2o"],
                    "answers": {k: {"choice": v} for k, v in profile().model_dump().items()},
                    "latency_ms": 3.0,
                    "deployment": "FP8 W8A16 / Marlin · test fixture",
                    "usage": {"input_tokens": 20, "output_tokens": 7},
                },
            )

        gateway_main.app.state.gateway_decision.client = httpx.AsyncClient(
            transport=httpx.MockTransport(gpu)
        )
        bedrock = MagicMock()
        bedrock.converse.return_value = {
            "output": {"message": {"content": [{"text": "重复请求只处理一次。"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 20, "outputTokens": 12},
        }
        gateway_main.app.state.bedrock = bedrock
        yield c


def body(session_id=None, **overrides):
    return {
        "task": {
            "text": "用一句中文解释幂等",
            "routing_preferences": {
                "benchmark_release": "2025-04-02",
                "allowed_models": ["amazon.nova-micro-v1:0"],
                "expected_output_tokens": 128,
                "max_output_tokens": 256,
            },
        },
        "provider": "h2o",
        "mode": "live",
        "execute_target": True,
        "catalog_version": routing.load_catalog()["version"],
        "ranking_version": routing.load_rankings()["version"],
        "policy_version": SELECTOR_VERSION,
        "session_id": session_id,
        **overrides,
    }


def execute(client, **overrides):
    response = client.post("/api/gateway/runs", json=body(**overrides))
    assert response.status_code == 200, response.text
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert events[-1]["type"] == "completed", events[-1]
    return events


def test_persists_full_trace_across_store_instances_and_login(client):
    session = client.post("/api/gateway/sessions", json={"title": "持久会话"}).json()
    events = execute(client, session_id=session["id"])
    run_id = events[0]["request_id"]
    store = History()
    saved = store.detail("tester", run_id)
    assert saved["status"] == "executed"
    assert saved["session_id"] == session["id"]
    assert saved["model_id"] == "amazon.nova-micro-v1:0"
    assert saved["reference_cost"] is not None
    assert len(saved["events"]) == len(events)
    assert "route_token" not in json.dumps(saved)
    client.post("/api/auth/logout", json={})
    assert client.get("/api/gateway/history").status_code == 401
    client.post("/api/auth/login", json=CREDS)
    assert client.get("/api/gateway/history").json()["items"][0]["id"] == run_id
    assert (
        client.get("/api/gateway/history/" + run_id).json()["request"]["task"]["text"]
        == "用一句中文解释幂等"
    )


def test_user_cannot_access_other_users_session_run_or_evaluation(client, auth_config):
    session = client.post("/api/gateway/sessions", json={"title": "私有"}).json()
    run_id = execute(client, session_id=session["id"])[0]["request_id"]
    save_user(auth_config, "other", "a-different-long-password")
    client.post("/api/auth/logout", json={})
    client.post(
        "/api/auth/login", json={"username": "other", "password": "a-different-long-password"}
    )
    assert client.get("/api/gateway/history").json()["items"] == []
    assert client.get("/api/gateway/history/" + run_id).status_code == 404
    assert (
        client.get("/api/gateway/history", params={"session_id": session["id"]}).status_code == 404
    )
    assert client.post("/api/gateway/runs", json=body(session_id=session["id"])).status_code == 404
    assert (
        client.post("/api/gateway/sessions/" + session["id"], json={"title": "changed"}).status_code
        == 404
    )
    assert client.get("/api/gateway/users").status_code == 403


def test_duplicate_submission_does_not_spend_again(client):
    payload = body(client_request_id="same-request-id-123456789")
    first = client.post("/api/gateway/runs", json=payload)
    assert first.status_code == 200
    count = gateway_main.app.state.bedrock.converse.call_count
    duplicate = client.post("/api/gateway/runs", json=payload)
    assert duplicate.status_code == 409
    assert gateway_main.app.state.bedrock.converse.call_count == count


def test_replays_excluded_unknown_cost_and_reviews_kept_separate(client):
    execute(client, mode="replay", execute_target=False)
    events = execute(client)
    run_id = events[0]["request_id"]
    assert (
        client.post(
            "/api/gateway/history/" + run_id + "/review",
            json={
                "passed": False,
                "rubric": "必须说明重复请求的副作用",
                "notes": "仅凭完整输出不能判定通过",
            },
        ).status_code
        == 200
    )
    stats = client.get("/api/gateway/performance").json()
    assert stats["live_requests"] == 1 and stats["replays"] == 1
    assert stats["completed"] == 1 and stats["business_reviews"] == 1
    assert stats["business_passes"] == 0
    assert stats["cost_known_samples"] == 1
    assert stats["route_p95_ms"] is not None
    assert routing.load_rankings()["version"]


def test_database_failure_stops_before_inference(client, monkeypatch):
    h = gateway_main.app.state.history
    monkeypatch.setattr(h, "begin", MagicMock(side_effect=RuntimeError("database unavailable")))
    response = client.post("/api/gateway/runs", json=body())
    assert response.status_code == 503
    gateway_main.app.state.bedrock.converse.assert_not_called()


def test_midstream_storage_failure_preserves_error_order_and_stops_target(client, monkeypatch):
    history = gateway_main.app.state.history
    original = history.event

    def fail_classification(user, item, event):
        if event.get("stage") == "classification" and event.get("status") == "completed":
            raise RuntimeError("storage unavailable")
        return original(user, item, event)

    monkeypatch.setattr(history, "event", fail_classification)
    response = client.post("/api/gateway/runs", json=body())
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert events[-1]["type"] == "error" and events[-1]["data"]["code"] == 503
    gateway_main.app.state.bedrock.converse.assert_not_called()


def test_model_failure_persisted_in_stats(client):
    from botocore.exceptions import ClientError

    gateway_main.app.state.bedrock.converse.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "limited"}},
        "Converse",
    )
    r = client.post("/api/gateway/runs", json=body())
    events = [json.loads(line) for line in r.text.splitlines() if line]
    assert events[-1]["type"] == "error"
    saved = client.get("/api/gateway/history/" + events[0]["request_id"]).json()
    assert saved["status"] == "error" and saved["failed_stage"] == "execution"
    stats = client.get("/api/gateway/performance").json()
    assert stats["failed"] == 1 and stats["cost_unknown_samples"] == 1
    assert stats["observed_reference_cost_usd"] is None


def test_legacy_routes_not_exposed_by_independent_app(client):
    for path in ("/api/crypto/catalog", "/api/gpu/status", "/api/comparison/catalog"):
        assert client.get(path).status_code == 404


def test_admin_create_disable_reset_and_password_revocation(client, auth_config):
    created = client.post(
        "/api/gateway/users",
        json={
            "username": "reviewer",
            "password": "reviewer-long-password",
        },
    )
    assert created.status_code == 201
    assert "password" not in created.text
    assert (
        client.post(
            "/api/gateway/users",
            json={
                "username": "reviewer",
                "password": "reviewer-long-password",
            },
        ).status_code
        == 409
    )
    assert client.post("/api/gateway/users/tester", json={"enabled": False}).status_code == 409
    assert client.post("/api/gateway/users/reviewer", json={"enabled": False}).status_code == 200
    with TestClient(gateway_main.app, base_url="https://testserver", headers=HEADERS) as other:
        assert (
            other.post(
                "/api/auth/login",
                json={
                    "username": "reviewer",
                    "password": "reviewer-long-password",
                },
            ).status_code
            == 401
        )
    old = client.cookies.get("decider_session")
    response = client.post(
        "/api/gateway/account/password",
        json={
            "current_password": CREDS["password"],
            "new_password": "new-strong-password-2026",
        },
    )
    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set("decider_session", old)
    assert client.get("/api/gateway/account").status_code == 401


def test_evaluation_records_actual_field_mismatches_without_target_calls(client):
    started = client.post(
        "/api/gateway/evaluations",
        json={
            "providers": ["h2o"],
            "case_ids": ["summarize", "sql-report"],
        },
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["id"]
    for _ in range(100):
        job = client.get("/api/gateway/evaluations/" + job_id).json()
        if job["status"] != "running":
            break
        time.sleep(0.01)
    assert job["status"] == "completed"
    assert job["summary"][0]["samples"] == 2
    assert job["summary"][0]["exact_matches"] == 1
    assert job["summary"][0]["field_accuracy"]["workflow"] == 0.5
    assert job["target_model_calls"] == 0
    assert all(row["deployment"] == "FP8 W8A16 / Marlin · test fixture" for row in job["results"])
    gateway_main.app.state.bedrock.converse.assert_not_called()
    assert client.get("/api/gateway/evaluations").json()["items"][0]["id"] == job_id
    assert (
        client.post(
            "/api/gateway/evaluations",
            json={
                "providers": ["h2o"],
                "case_ids": ["invented"],
            },
        ).status_code
        == 422
    )


@pytest.mark.parametrize("dynamo", [False, True])
def test_records_pagination_isolation_expiry_and_atomic_create(tmp_path, dynamo, monkeypatch):
    with mock_aws():
        if dynamo:
            monkeypatch.setenv("AWS_REGION", "us-east-1")
            db = boto3.client("dynamodb", region_name="us-east-1")
            db.create_table(
                TableName="records",
                BillingMode="PAY_PER_REQUEST",
                KeySchema=[
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "pk", "AttributeType": "S"},
                    {"AttributeName": "sk", "AttributeType": "S"},
                ],
            )
            store = Records(table="records")
        else:
            store = Records(path=tmp_path / "records.db")
        for i in range(5):
            store.put("USER#one", f"RUN#{i:03d}", {"id": i, "cost": "0.000001"})
        store.put("USER#two", "RUN#900", {"id": "private"})
        one = store.query("USER#one", "RUN#", limit=2)
        two = store.query("USER#one", "RUN#", limit=2, cursor=one["next_cursor"])
        assert [r["id"] for r in one["items"] + two["items"]] == [4, 3, 2, 1]
        assert store.get("USER#one", "RUN#900") is None
        with pytest.raises(ClientError if dynamo else sqlite3.IntegrityError):
            store.put_many([("USER#one", "RUN#004", {}), ("USER#one", "NEW", {})], create=True)
        assert store.get("USER#one", "NEW") is None
        store.put("USER#one", "EXPIRED", {"expires_at": int(time.time()) - 1})
        assert store.get("USER#one", "EXPIRED") is None
