"""Client stubs shared by the per-platform client tests.

Lifted as-is from the private helpers in tests/test_facebook.py, made public and nothing more. If a
platform needs different behaviour it gets its own local helper rather than these growing a knob.

A plain module, not conftest.py, because only fixtures auto-inject from conftest and these are
called directly. tests/test_facebook.py still has its own copies; migrating it is separate.
"""

from types import SimpleNamespace

import requests


def http_error(status_code: int = 400) -> requests.HTTPError:
    err = requests.HTTPError(f"{status_code} error")
    err.response = SimpleNamespace(status_code=status_code)
    return err


def make_fake_get(metric_names: list[str], failing_metric: str, metric_value=42):
    """Simulates the Graph API: the joined batch request 400s (as it does whenever any
    one metric is bad), then each per-metric request succeeds except `failing_metric`."""
    joined = ",".join(metric_names)

    def fake_get(path, params=None, retries=3):
        metric = params["metric"]
        if metric in (joined, failing_metric):
            raise http_error(400)
        return {"data": [{"name": metric, "period": "lifetime", "values": [{"value": metric_value}]}]}

    return fake_get


def paging(after=None, next_url=None) -> dict:
    result = {}
    if next_url:
        result["next"] = next_url
    if after:
        result["cursors"] = {"after": after}
    return result


def stub_pages(monkeypatch, client, pages: list[dict]) -> list[tuple[str, dict]]:
    """Serve `pages` in order, recording each (path, params) the client asks for."""
    calls: list[tuple[str, dict]] = []

    def fake_get(path, params=None, retries=3):
        calls.append((path, dict(params or {})))
        return pages[min(len(calls) - 1, len(pages) - 1)]

    monkeypatch.setattr(client, "_get", fake_get)
    return calls
