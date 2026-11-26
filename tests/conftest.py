import pytest

from app.auth import SQLiteStore, hash_password

PASSWORD = "test-password-with-entropy"


@pytest.fixture(autouse=True)
def anonymous_gpu_transport_fixtures(monkeypatch):
    from app import gateway_evidence_input
    from tests.gateway_fakes import ANONYMOUS_ALIASES, payload_key
    original = gateway_evidence_input.selection_payload
    ANONYMOUS_ALIASES.clear()

    def capture(task, state, aliases, previous):
        payload = original(task, state, aliases, previous)
        ANONYMOUS_ALIASES[payload_key(payload)] = aliases
        return payload

    monkeypatch.setattr(gateway_evidence_input, "selection_payload", capture)
    yield
    ANONYMOUS_ALIASES.clear()


@pytest.fixture(scope="session")
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def auth_config(tmp_path, monkeypatch, password_hash):
    monkeypatch.delenv("AUTH_TABLE", raising=False)
    monkeypatch.delenv("AUTH_ORIGIN", raising=False)
    monkeypatch.delenv("GPU_STATE_TABLE", raising=False)
    for name in ("JEV_API_KEY", "JEV_API_KEY_FILE", "JEV_FUNCTION_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "auth.sqlite3"))
    store = SQLiteStore(tmp_path / "auth.sqlite3")
    store.put(
        {
            "pk": "USER#tester",
            "username": "tester",
            "password_hash": password_hash,
            "enabled": True,
            "version": "test-version",
        }
    )
    return store
