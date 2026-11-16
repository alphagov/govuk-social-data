import json

import pytest
import requests

from src.meta import client as client_module
from src.meta.client import GraphClient, redact_token

TOKEN = "EAAtesttoken123"


class StubClient(GraphClient):
    BASE_URL = "https://graph.example.com/v25.0"


def _response(status_code: int, body: dict | None = None, reason: str = "") -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.reason = reason
    response.url = f"https://graph.example.com/v25.0/123/insights?access_token={TOKEN}&metric=reach"
    response._content = json.dumps(body or {}).encode()
    return response


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(client_module.time, "sleep", lambda seconds: None)
    return StubClient(access_token=TOKEN)


def _serve(monkeypatch, client, outcomes):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(client._session, "get", fake_get)
    return calls


def test_redact_token_hides_token_in_urls():
    text = f"GET https://graph.example.com/me?access_token={TOKEN}&fields=id"

    assert redact_token(text) == "GET https://graph.example.com/me?access_token=REDACTED&fields=id"


def test_200_returns_json(monkeypatch, client):
    _serve(monkeypatch, client, [_response(200, {"data": [{"id": "1"}]})])

    assert client._get("123/insights") == {"data": [{"id": "1"}]}


def test_400_includes_meta_message_and_hides_token(monkeypatch, client, caplog):
    meta_error = {
        "error": {
            "message": "(#10) Application does not have permission for this action",
            "type": "OAuthException",
            "code": 10,
        }
    }
    calls = _serve(monkeypatch, client, [_response(400, meta_error, "Bad Request")])

    with pytest.raises(requests.HTTPError) as exc_info:
        client._get("123/insights")

    message = str(exc_info.value)
    assert "Application does not have permission for this action" in message
    assert "OAuthException, code 10" in message
    assert TOKEN not in message
    assert "access_token=REDACTED" in message
    assert exc_info.value.response.status_code == 400
    assert len(calls) == 1
    assert TOKEN not in caplog.text


def test_400_without_json_body_still_raises(monkeypatch, client):
    response = _response(400, reason="Bad Request")
    response._content = b"not json"
    _serve(monkeypatch, client, [response])

    with pytest.raises(requests.HTTPError) as exc_info:
        client._get("123/insights")

    assert str(exc_info.value).startswith("400 Client Error: Bad Request")
    assert TOKEN not in str(exc_info.value)


def test_429_waits_then_retries(monkeypatch, client, caplog):
    rate_limited = _response(429, {"error": {"message": "Too many calls", "code": 4}}, "Too Many Requests")
    calls = _serve(monkeypatch, client, [rate_limited, _response(200, {"data": []})])

    assert client._get("123/insights") == {"data": []}
    assert len(calls) == 2
    assert "Rate limited" in caplog.text
    assert TOKEN not in caplog.text


def test_429_on_every_attempt_raises_after_retries(monkeypatch, client):
    rate_limited = _response(429, {"error": {"message": "Too many calls", "code": 4}}, "Too Many Requests")
    calls = _serve(monkeypatch, client, [rate_limited])

    with pytest.raises(requests.HTTPError) as exc_info:
        client._get("123/insights", retries=3)

    assert exc_info.value.response.status_code == 429
    assert "Too many calls" in str(exc_info.value)
    assert len(calls) == 3


def test_500_retries_then_succeeds(monkeypatch, client):
    calls = _serve(monkeypatch, client, [_response(500, reason="Internal Server Error"), _response(200, {"ok": 1})])

    assert client._get("123/insights") == {"ok": 1}
    assert len(calls) == 2


def test_connection_error_is_redacted(monkeypatch, client, caplog):
    error = requests.exceptions.ConnectionError(f"Max retries exceeded with url: /123?access_token={TOKEN}")
    _serve(monkeypatch, client, [error])

    with pytest.raises(requests.exceptions.ConnectionError) as exc_info:
        client._get("123/insights")

    assert TOKEN not in str(exc_info.value)
    assert TOKEN not in caplog.text


def test_absolute_next_link_is_redacted_in_logs(monkeypatch, client, caplog):
    next_link = f"https://graph.example.com/v25.0/123/insights?access_token={TOKEN}&after=abc"
    _serve(monkeypatch, client, [_response(500, reason="Internal Server Error"), _response(200, {})])

    client._get(next_link)

    assert TOKEN not in caplog.text
    assert "access_token=REDACTED" in caplog.text
