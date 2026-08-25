"""
HTTP-level integration tests for the Instagram Graph API.

These tests bypass the InstagramClient Python class entirely and call the
Graph API directly with requests.get, one metric per test. This provides an
independent check that each endpoint and metric string is valid — separate from
whether the Python client handles the response correctly.

Run with:
    poetry run pytest tests/test_instagram_http.py -m integration -v
"""

from datetime import date, timedelta

import pytest
import requests

from src.authentication import get_gcp_secret

GRAPH_VERSION = "v25.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"

TOKEN_SECRET = "instagram_bearer_token"
IG_USER_ID_SECRET = "instagram_user_id"
PROJECT_ID = "gds-social-data"

# Metric lists — mirrors what the example code uses.
# If any parametrized test fails, that metric should be removed from the client constants.
ACCOUNT_DAY_METRICS = [
    "reach",
    "follower_count",
]

ACCOUNT_TOTAL_VALUE_METRICS = [
    "views",
]

MEDIA_LIFETIME_METRICS = [
    "reach",
    "views",
    "likes",
    "comments",
    "saved",
    "shares",
    "total_interactions",
]

# Field list requested from the /media edge — mirrors instagram.api.get_media.
MEDIA_FIELDS = ",".join(
    [
        "id",
        "caption",
        "media_type",
        "media_product_type",
        "permalink",
        "thumbnail_url",
        "media_url",
        "timestamp",
        "like_count",
        "comments_count",
    ]
)

# Date window used for day-period account insight queries.
# follower_count only supports the last 30 days, so keep this window recent.
_UNTIL = (date.today() - timedelta(days=10)).isoformat()
_SINCE = (date.today() - timedelta(days=15)).isoformat()


# ------------------------------------------------------------------
# Module-scoped fixture — token + IDs fetched once for the whole session
# ------------------------------------------------------------------


@pytest.fixture(scope="module")
def _http():
    """
    Returns a dict with:
      - token:    raw access token string
      - ig_user_id: Instagram Business User ID
      - media_id: most recent media item ID (used for media insight tests)
    """
    import logging

    logging.disable(logging.CRITICAL)

    token = get_gcp_secret(TOKEN_SECRET, project_id=PROJECT_ID)
    ig_user_id = get_gcp_secret(IG_USER_ID_SECRET, project_id=PROJECT_ID).strip()

    # Fetch one media item to use in per-media-insight tests
    media_url = f"{BASE_URL}/{ig_user_id}/media"
    r = requests.get(
        media_url,
        params={
            "access_token": token,
            "fields": "id",
            "limit": "1",
        },
        timeout=30,
    )
    media_id = None
    if r.status_code == 200:
        data = r.json().get("data", [])
        if data:
            media_id = data[0]["id"]

    return {"token": token, "ig_user_id": ig_user_id, "media_id": media_id}


def _get(path: str, token: str, params: dict | None = None) -> requests.Response:
    """Helper: GET {BASE_URL}/{path} with access_token injected."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    return requests.get(url, params={"access_token": token, **(params or {})}, timeout=30)


# ------------------------------------------------------------------
# 1. Account node (info)
# ------------------------------------------------------------------


@pytest.mark.integration
def test_http_ig_account_node(_http):
    """GET /{ig_user_id}?fields=id,username,name,biography,followers_count,follows_count,media_count"""
    r = _get(
        _http["ig_user_id"],
        _http["token"],
        {"fields": "id,username,name,biography,followers_count,follows_count,media_count"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("id") == _http["ig_user_id"]
    assert "username" in data


# ------------------------------------------------------------------
# 2. Account/insights — day metrics (per metric)
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("metric", ACCOUNT_DAY_METRICS)
def test_http_ig_account_day_metric(_http, metric):
    """GET /{ig_user_id}/insights?metric={metric}&period=day&since=...&until=..."""
    r = _get(
        f"{_http['ig_user_id']}/insights",
        _http["token"],
        {"metric": metric, "period": "day", "since": _SINCE, "until": _UNTIL},
    )
    assert r.status_code == 200, f"metric={metric!r} → HTTP {r.status_code}: {r.text}"
    assert "data" in r.json(), f"metric={metric!r} response missing 'data' key: {r.text}"


# ------------------------------------------------------------------
# 3. Account/insights — total_value metrics (per metric)
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("metric", ACCOUNT_TOTAL_VALUE_METRICS)
def test_http_ig_account_total_value_metric(_http, metric):
    """GET /{ig_user_id}/insights?metric={metric}&metric_type=total_value&period=day&since=...&until=..."""
    r = _get(
        f"{_http['ig_user_id']}/insights",
        _http["token"],
        {
            "metric": metric,
            "metric_type": "total_value",
            "period": "day",
            "since": _SINCE,
            "until": _UNTIL,
        },
    )
    assert r.status_code == 200, f"metric={metric!r} → HTTP {r.status_code}: {r.text}"
    assert "data" in r.json(), f"metric={metric!r} response missing 'data' key: {r.text}"


# ------------------------------------------------------------------
# 4. Account/media edge
# ------------------------------------------------------------------


@pytest.mark.integration
def test_http_ig_media_edge(_http):
    """GET /{ig_user_id}/media?fields=id,caption,media_type,media_product_type,..."""
    r = _get(
        f"{_http['ig_user_id']}/media",
        _http["token"],
        {
            "fields": MEDIA_FIELDS,
            "limit": "10",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "data" in data
    assert len(data["data"]) > 0, "Expected at least one media item"


@pytest.mark.integration
def test_http_ig_media_edge_pages_by_cursor(_http):
    """This checks /media edge returns a cursor, not just an absolute next link.

    It matters because loads are WRITE_TRUNCATE per target_date: if this edge stopped offering
    cursors and the fallback were ever wrong, a silent truncation would replace full history
    with a single page and raise nothing.

    Requests exactly what instagram.api.get_media requests, so the answer describes production
    rather than a convenient smaller query.
    """
    r = _get(
        f"{_http['ig_user_id']}/media",
        _http["token"],
        {"fields": MEDIA_FIELDS, "limit": "100"},
    )
    assert r.status_code == 200, r.text
    paging = r.json().get("paging", {})
    assert "next" in paging, "account has one page at limit=100 — cannot confirm cursor behaviour"
    assert paging.get("cursors", {}).get("after"), f"no cursors.after in paging: {paging}"


# ------------------------------------------------------------------
# 5. Media/insights — per metric (lifetime)
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("metric", MEDIA_LIFETIME_METRICS)
def test_http_ig_media_insight(_http, metric):
    """GET /{media_id}/insights?metric={metric}&period=lifetime"""
    if not _http["media_id"]:
        pytest.skip("No media_id available")
    r = _get(
        f"{_http['media_id']}/insights",
        _http["token"],
        {"metric": metric, "period": "lifetime"},
    )
    assert r.status_code == 200, f"metric={metric!r} → HTTP {r.status_code}: {r.text}"
    assert "data" in r.json(), f"metric={metric!r} response missing 'data' key: {r.text}"
