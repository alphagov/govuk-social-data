"""HTTP-level integration tests for the Threads Graph API.

These bypass ThreadsClient entirely and call the API with requests.get, one metric per test, so a
failure names the endpoint or metric string rather than the Python handling of it. Every constant
under test is imported from threads.api, so the two cannot drift: a failure here is fixed by editing
threads/api.py, never by editing this file.

Run with:
    poetry run pytest tests/test_threads_http.py -m integration -v
"""

from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from src.authentication import get_gcp_secret
from threads.api import (
    ACCOUNT_LIFETIME_METRICS,
    ACCOUNT_TIME_SERIES_METRICS,
    ACCOUNT_TOTAL_VALUE_METRICS,
    BASE_URL,
    DEMOGRAPHIC_BREAKDOWNS,
    DEMOGRAPHIC_METRIC,
    INSIGHTS_RETENTION_DAYS,
    MEDIA_FIELDS,
    MEDIA_METRICS,
    PROFILE_FIELDS,
    PROJECT_ID,
    REPLY_FIELDS,
    TOKEN_SECRET,
    USER_ID_SECRET,
)

ACCOUNT_WINDOW_METRICS = ACCOUNT_TIME_SERIES_METRICS + ACCOUNT_TOTAL_VALUE_METRICS


def _unix(d: date) -> int:
    """Midnight-UTC Unix timestamp. Computed here rather than imported, so this file stays an
    independent check on threads.api._to_unix rather than a restatement of it."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


# A recent window: insight retention is not documented, so stay well inside anything plausible.
_SINCE = _unix(date.today() - timedelta(days=15))
_UNTIL = _unix(date.today() - timedelta(days=10))


@pytest.fixture(scope="module")
def _http():
    """Returns token, threads_user_id, and one media id for the media-insight tests."""
    import logging

    logging.disable(logging.CRITICAL)

    token = get_gcp_secret(TOKEN_SECRET, project_id=PROJECT_ID).strip()
    threads_user_id = get_gcp_secret(USER_ID_SECRET, project_id=PROJECT_ID).strip()

    r = _get(f"{threads_user_id}/threads", token, {"fields": "id", "limit": "1"})
    media_id = None
    if r.status_code == 200:
        data = r.json().get("data", [])
        if data:
            media_id = data[0]["id"]

    return {"token": token, "threads_user_id": threads_user_id, "media_id": media_id}


def _get(path: str, token: str, params: dict | None = None) -> requests.Response:
    """Helper: GET {BASE_URL}/{path} with access_token injected."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    return requests.get(url, params={"access_token": token, **(params or {})}, timeout=30)


# ------------------------------------------------------------------
# 1. User node (profile)
# ------------------------------------------------------------------


@pytest.mark.integration
def test_http_threads_profile_node(_http):
    """GET /{threads_user_id}?fields=PROFILE_FIELDS"""
    r = _get(_http["threads_user_id"], _http["token"], {"fields": PROFILE_FIELDS})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("id") == _http["threads_user_id"]
    assert "username" in data


@pytest.mark.integration
@pytest.mark.parametrize("field", PROFILE_FIELDS.split(","))
def test_http_threads_profile_field(_http, field):
    """Each PROFILE_FIELDS entry is accepted on its own, so one bad name is named by the failure."""
    r = _get(_http["threads_user_id"], _http["token"], {"fields": field})
    assert r.status_code == 200, f"field={field!r} → HTTP {r.status_code}: {r.text}"


# ------------------------------------------------------------------
# 2. User/threads edge
# ------------------------------------------------------------------


@pytest.mark.integration
def test_http_threads_media_edge(_http):
    """GET /{threads_user_id}/threads?fields=MEDIA_FIELDS"""
    r = _get(f"{_http['threads_user_id']}/threads", _http["token"], {"fields": MEDIA_FIELDS, "limit": "10"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "data" in data
    assert len(data["data"]) > 0, "Expected at least one media item"


@pytest.mark.integration
def test_http_threads_media_edge_pages_by_cursor(_http):
    """The /threads edge must return a cursor, not just an absolute next link.

    It matters because loads are WRITE_TRUNCATE per target_date: GraphClient._next_page prefers
    cursors and falls back to the absolute link, and if that fallback were ever wrong a silent
    truncation would replace full history with a single page and raise nothing.

    Requests exactly what ThreadsClient.get_media requests, so the answer describes production.
    """
    r = _get(f"{_http['threads_user_id']}/threads", _http["token"], {"fields": MEDIA_FIELDS, "limit": "100"})
    assert r.status_code == 200, r.text
    paging = r.json().get("paging", {})
    assert "next" in paging, "account has one page at limit=100 — cannot confirm cursor behaviour"
    assert paging.get("cursors", {}).get("after"), f"no cursors.after in paging: {paging}"


# ------------------------------------------------------------------
# 3. User/replies edge
# ------------------------------------------------------------------


@pytest.mark.integration
def test_http_threads_replies_edge(_http):
    """GET /{threads_user_id}/replies?fields=REPLY_FIELDS — needs the threads_read_replies scope."""
    r = _get(f"{_http['threads_user_id']}/replies", _http["token"], {"fields": REPLY_FIELDS, "limit": "10"})
    assert r.status_code == 200, r.text
    assert "data" in r.json()


# ------------------------------------------------------------------
# 4. User/threads_insights edge — account metrics
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("metric", ACCOUNT_WINDOW_METRICS)
def test_http_threads_account_window_metric(_http, metric):
    """GET /{threads_user_id}/threads_insights?metric={metric}&since=...&until=... (unix seconds)"""
    r = _get(
        f"{_http['threads_user_id']}/threads_insights",
        _http["token"],
        {"metric": metric, "since": _SINCE, "until": _UNTIL},
    )
    assert r.status_code == 200, f"metric={metric!r} → HTTP {r.status_code}: {r.text}"
    assert "data" in r.json(), f"metric={metric!r} response missing 'data' key: {r.text}"


@pytest.mark.integration
@pytest.mark.parametrize("metric", ACCOUNT_LIFETIME_METRICS)
def test_http_threads_account_lifetime_metric(_http, metric):
    """Lifetime metrics take no since/until — passing them is what this asserts is unnecessary."""
    r = _get(f"{_http['threads_user_id']}/threads_insights", _http["token"], {"metric": metric})
    assert r.status_code == 200, f"metric={metric!r} → HTTP {r.status_code}: {r.text}"
    assert "data" in r.json(), f"metric={metric!r} response missing 'data' key: {r.text}"


@pytest.mark.integration
def test_http_threads_account_metrics_batch(_http):
    """All window metrics in one request — the batch shape _fetch_insight_items tries first."""
    r = _get(
        f"{_http['threads_user_id']}/threads_insights",
        _http["token"],
        {"metric": ",".join(ACCOUNT_WINDOW_METRICS), "since": _SINCE, "until": _UNTIL},
    )
    assert r.status_code == 200, r.text
    names = {item["name"] for item in r.json().get("data", [])}
    assert names == set(ACCOUNT_WINDOW_METRICS), f"batch returned {sorted(names)}"


@pytest.mark.integration
def test_http_threads_account_clicks_unsupported(_http):
    """`clicks` is not servable on the account edge, which is why it lives in MEDIA_METRICS.

    The API's own validation error lists it as a valid metric name, so this is not a typo — the
    request is simply refused ("Unsupported request - method type: get"), with or without a window.
    Should this ever start returning 200, clicks is worth revisiting at account level; until then
    re-adding it to the account constants breaks every daily run.
    """
    r = _get(
        f"{_http['threads_user_id']}/threads_insights",
        _http["token"],
        {"metric": "clicks", "since": _SINCE, "until": _UNTIL},
    )
    assert r.status_code >= 400, f"account-level clicks now works — reconsider the constants: {r.text}"


# ------------------------------------------------------------------
# 5. User/threads_insights edge — follower demographics
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("breakdown", DEMOGRAPHIC_BREAKDOWNS)
def test_http_threads_follower_demographics(_http, breakdown):
    """One request per breakdown; the edge accepts only one at a time and no since/until."""
    r = _get(
        f"{_http['threads_user_id']}/threads_insights",
        _http["token"],
        {"metric": DEMOGRAPHIC_METRIC, "breakdown": breakdown},
    )
    assert r.status_code == 200, f"breakdown={breakdown!r} → HTTP {r.status_code}: {r.text}"
    data = r.json().get("data", [])
    assert data, f"breakdown={breakdown!r} returned no data — below the 100-follower floor? {r.text}"
    # The shape threads.api._parse_demographics reads first. If this ever fails while the request
    # succeeds, the fallback branch in that parser is what is now carrying the load.
    breakdowns = data[0].get("total_value", {}).get("breakdowns", [])
    assert breakdowns, f"breakdown={breakdown!r} has no total_value.breakdowns: {data[0]}"
    assert breakdowns[0].get("dimension_keys") == [breakdown], breakdowns[0]
    assert breakdowns[0].get("results"), breakdowns[0]


@pytest.mark.integration
def test_http_threads_follower_demographics_combined_is_a_cross_product(_http):
    """Several breakdowns in one request are accepted, but answer with their cross-product.

    Documents why get_follower_demographics loops one at a time anyway: the combined response pairs
    the dimensions (dimension_keys ["age","gender"] → dimension_values ["25-34","F"]), which does not
    fit the one-dimension-per-row grain of threads_follower_demographics.
    """
    pair = DEMOGRAPHIC_BREAKDOWNS[:2]
    r = _get(
        f"{_http['threads_user_id']}/threads_insights",
        _http["token"],
        {"metric": DEMOGRAPHIC_METRIC, "breakdown": ",".join(pair)},
    )
    assert r.status_code == 200, r.text
    breakdowns = r.json()["data"][0]["total_value"]["breakdowns"]
    assert breakdowns[0]["dimension_keys"] == pair, breakdowns[0]["dimension_keys"]
    assert len(breakdowns[0]["results"][0]["dimension_values"]) == len(pair), breakdowns[0]["results"][0]


# ------------------------------------------------------------------
# 6. Media/insights edge
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("metric", MEDIA_METRICS)
def test_http_threads_media_insight(_http, metric):
    """GET /{media_id}/insights?metric={metric} — this edge takes no period parameter."""
    if not _http["media_id"]:
        pytest.skip("No media_id available")
    r = _get(f"{_http['media_id']}/insights", _http["token"], {"metric": metric})
    assert r.status_code == 200, f"metric={metric!r} → HTTP {r.status_code}: {r.text}"
    assert "data" in r.json(), f"metric={metric!r} response missing 'data' key: {r.text}"


# ------------------------------------------------------------------
# 7. Temporal boundary — the rolling retention window
# ------------------------------------------------------------------

# The oldest date threads.api._to_unix will allow, and the first one it will not.
_OLDEST_ALLOWED = date.today() - timedelta(days=INSIGHTS_RETENTION_DAYS - 1)
_FIRST_REFUSED = date.today() - timedelta(days=INSIGHTS_RETENTION_DAYS)


@pytest.mark.integration
def test_http_threads_insights_accept_oldest_retained_day(_http):
    """The oldest day _to_unix allows is genuinely accepted, so the guard is not a day too strict."""
    r = _get(
        f"{_http['threads_user_id']}/threads_insights",
        _http["token"],
        {
            "metric": ACCOUNT_TIME_SERIES_METRICS[0],
            "since": _unix(_OLDEST_ALLOWED),
            "until": _unix(_OLDEST_ALLOWED + timedelta(days=1)),
        },
    )
    assert r.status_code == 200, f"{_OLDEST_ALLOWED.isoformat()} was rejected: {r.text}"


@pytest.mark.integration
def test_http_threads_insights_reject_beyond_retention(_http):
    """And the day before it is a fatal 400 — what _to_unix's ValueError exists to pre-empt.

    A failure either way means INSIGHTS_RETENTION_DAYS is wrong and threads/api.py needs the edit.
    """
    r = _get(
        f"{_http['threads_user_id']}/threads_insights",
        _http["token"],
        {
            "metric": ACCOUNT_TIME_SERIES_METRICS[0],
            "since": _unix(_FIRST_REFUSED),
            "until": _unix(_FIRST_REFUSED + timedelta(days=1)),
        },
    )
    assert r.status_code >= 400, f"{_FIRST_REFUSED.isoformat()} was accepted: {r.text}"
