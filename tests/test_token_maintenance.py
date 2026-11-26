import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import requests

import src.meta.token_maintenance as tm

NOW = datetime(2026, 12, 1, tzinfo=UTC)
SECRETS = {
    "facebook_page_token": json.dumps({"access_token": "EAAfacebooktoken"}),
    "instagram_bearer_token": "EAAinstagramtoken",
    "facebook_app_id": "111",
    "facebook_app_secret": "fbsecret",
    "instagram_app_id": "222",
    "instagram_app_secret": "igsecret",
    "threads_long_lived_access_token": "THthreadstoken",
}


def _response(status: int, body: dict) -> SimpleNamespace:
    return SimpleNamespace(ok=status < 400, status_code=status, json=lambda: body)


def _timestamp(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp())


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    monkeypatch.setattr(tm, "get_gcp_secret", lambda name, project_id=None: SECRETS[name])


def _serve_debug(monkeypatch, by_token: dict):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return by_token[params["input_token"]]

    monkeypatch.setattr(tm.requests, "get", fake_get)
    return calls


def test_token_expiry_takes_the_earliest_limit_and_ignores_zero():
    assert tm.token_expiry({"expires_at": 0, "data_access_expires_at": 0}) is None
    assert tm.token_expiry(
        {"expires_at": _timestamp("2026-12-26"), "data_access_expires_at": 0}
    ).date().isoformat() == ("2026-12-26")
    earliest = tm.token_expiry(
        {"expires_at": _timestamp("2026-12-26"), "data_access_expires_at": _timestamp("2026-12-10")}
    )
    assert earliest.date().isoformat() == "2026-12-10"


def test_check_expiry_passes_when_tokens_have_time_left(monkeypatch):
    healthy = _response(200, {"data": {"is_valid": True, "expires_at": _timestamp("2027-01-20")}})
    never = _response(200, {"data": {"is_valid": True, "expires_at": 0}})
    calls = _serve_debug(monkeypatch, {"EAAfacebooktoken": never, "EAAinstagramtoken": healthy})

    assert tm.check_expiry(NOW) == []
    assert calls[0]["access_token"] == "111|fbsecret"
    assert calls[1]["access_token"] == "222|igsecret"


def test_check_expiry_flags_a_token_within_fourteen_days(monkeypatch):
    soon = _response(200, {"data": {"is_valid": True, "expires_at": _timestamp("2026-12-06")}})
    never = _response(200, {"data": {"is_valid": True, "expires_at": 0}})
    _serve_debug(monkeypatch, {"EAAfacebooktoken": never, "EAAinstagramtoken": soon})

    problems = tm.check_expiry(NOW)

    assert len(problems) == 1
    assert "Instagram token expires on 2026-12-06" in problems[0]


def test_check_expiry_flags_an_invalid_token(monkeypatch):
    invalid = _response(200, {"data": {"is_valid": False}})
    never = _response(200, {"data": {"is_valid": True, "expires_at": 0}})
    _serve_debug(monkeypatch, {"EAAfacebooktoken": invalid, "EAAinstagramtoken": never})

    assert tm.check_expiry(NOW) == ["Facebook token is no longer valid, rotate it now"]


def test_errors_never_contain_tokens(monkeypatch, caplog):
    def fake_get(url, params=None, timeout=None):
        if params["input_token"] == "EAAfacebooktoken":
            raise requests.ConnectionError(
                f"Max retries exceeded with url: /debug_token?input_token={params['input_token']}"
            )
        return _response(400, {"error": {"message": "Invalid OAuth access token"}})

    monkeypatch.setattr(tm.requests, "get", fake_get)

    problems = tm.check_expiry(NOW)

    assert len(problems) == 2
    for text in [*problems, caplog.text]:
        assert "EAAfacebooktoken" not in text
        assert "EAAinstagramtoken" not in text
        assert "secret" not in text
    assert "Invalid OAuth access token" in problems[1]


def test_refresh_threads_token_saves_the_new_token(monkeypatch):
    saved = []
    monkeypatch.setattr(
        tm.requests,
        "get",
        lambda url, params=None, timeout=None: _response(200, {"access_token": "THnewtoken", "expires_in": 5184000}),
    )
    monkeypatch.setattr(
        tm.secretmanager,
        "SecretManagerServiceClient",
        lambda: SimpleNamespace(add_secret_version=lambda request: saved.append(request)),
    )

    expires_at = tm.refresh_threads_token()

    assert saved[0]["parent"] == "projects/gds-social-data/secrets/threads_long_lived_access_token"
    assert saved[0]["payload"]["data"] == b"THnewtoken"
    assert (expires_at - datetime.now(UTC)).days in (59, 60)


def test_refresh_threads_token_fails_without_saving(monkeypatch):
    saved = []
    monkeypatch.setattr(
        tm.requests,
        "get",
        lambda url, params=None, timeout=None: _response(400, {"error": {"message": "Session has expired"}}),
    )
    monkeypatch.setattr(
        tm.secretmanager,
        "SecretManagerServiceClient",
        lambda: SimpleNamespace(add_secret_version=lambda request: saved.append(request)),
    )

    with pytest.raises(RuntimeError, match="Session has expired") as exc_info:
        tm.refresh_threads_token()

    assert saved == []
    assert "THthreadstoken" not in str(exc_info.value)
