"""
HTTP-level integration tests for the Facebook Graph API.

These tests bypass the FacebookClient Python abstraction entirely and call the
Graph API directly with requests.get, one metric per test. This provides an
independent check that each endpoint and metric string is valid — separate from
whether the Python client handles the response correctly.

Run with:
    poetry run pytest tests/test_facebook_http.py -m integration -v
"""

import pytest
import requests

from facebook.api import (
    FB_GRAPH_BASE,
    PAGE_DAY_METRICS,
    PAGE_ID_SECRET,
    PAGE_LIFETIME_METRICS,
    POST_METRICS,
    PROJECT_ID,
    VIDEO_METRICS,
    build_client,
)
from src.authentication import get_gcp_secret

# Date window used for day-period page insight queries (well within 93-day limit)
_SINCE = "2025-01-01"
_UNTIL = "2025-01-07"


# ------------------------------------------------------------------
# Module-scoped fixture — token + IDs fetched once for the whole session
# ------------------------------------------------------------------


@pytest.fixture(scope="module")
def _http():
    """
    Returns a dict with:
      - token:    raw access token string (extracted from session.params)
      - page_id:  Facebook Page ID
      - post_id:  most recent post ID
      - video_id: most recent video ID
    """
    import logging

    logging.disable(logging.CRITICAL)

    client = build_client()
    page_id = get_gcp_secret(PAGE_ID_SECRET, project_id=PROJECT_ID)

    # Extract the raw token from the session so we can pass it directly
    token = client._session.params["access_token"]

    # Use _get (not _paginate) — we only need the first item, not all pages
    posts_data = client._get(f"{page_id}/posts", params={"fields": "id", "limit": "1"})
    post_id = posts_data["data"][0]["id"] if posts_data.get("data") else None

    videos_data = client._get(f"{page_id}/videos", params={"fields": "id", "limit": "1"})
    video_id = videos_data["data"][0]["id"] if videos_data.get("data") else None

    return {"token": token, "page_id": page_id, "post_id": post_id, "video_id": video_id}


def _get(path: str, token: str, params: dict | None = None) -> requests.Response:
    """Helper: GET {FB_GRAPH_BASE}/{path} with access_token injected."""
    url = f"{FB_GRAPH_BASE}/{path.lstrip('/')}"
    return requests.get(url, params={"access_token": token, **(params or {})}, timeout=30)


# ------------------------------------------------------------------
# 1. Page node (info)
# ------------------------------------------------------------------


@pytest.mark.integration
def test_http_page_node(_http):
    """GET /{page_id}?fields=id,name,fan_count,followers_count,talking_about_count"""
    r = _get(
        _http["page_id"],
        _http["token"],
        {"fields": "id,name,fan_count,followers_count,talking_about_count"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("id") == _http["page_id"]
    assert "name" in data


# ------------------------------------------------------------------
# 2. Page/insights — day metrics
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("metric", PAGE_DAY_METRICS)
def test_http_page_day_metric(_http, metric):
    """GET /{page_id}/insights?metric={metric}&period=day&since=...&until=..."""
    r = _get(
        f"{_http['page_id']}/insights",
        _http["token"],
        {"metric": metric, "period": "day", "since": _SINCE, "until": _UNTIL},
    )
    assert r.status_code == 200, f"metric={metric!r} → HTTP {r.status_code}: {r.text}"
    assert "data" in r.json(), f"metric={metric!r} response missing 'data' key: {r.text}"


# ------------------------------------------------------------------
# 2b. Page/insights — lifetime metrics
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("metric", PAGE_LIFETIME_METRICS)
def test_http_page_lifetime_metric(_http, metric):
    """GET /{page_id}/insights?metric={metric}&period=lifetime"""
    r = _get(
        f"{_http['page_id']}/insights",
        _http["token"],
        {"metric": metric, "period": "lifetime"},
    )
    assert r.status_code == 200, f"metric={metric!r} → HTTP {r.status_code}: {r.text}"
    assert "data" in r.json(), f"metric={metric!r} response missing 'data' key: {r.text}"


# ------------------------------------------------------------------
# 3. Page/posts edge
# ------------------------------------------------------------------


@pytest.mark.integration
def test_http_posts_edge(_http):
    """GET /{page_id}/posts?fields=id,message,created_time,permalink_url,attachments{type}"""
    r = _get(
        f"{_http['page_id']}/posts",
        _http["token"],
        {"fields": "id,message,created_time,permalink_url,attachments{type}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "data" in data
    assert len(data["data"]) > 0, "Expected at least one post"


# ------------------------------------------------------------------
# 4. Post/insights — per metric
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("metric", POST_METRICS)
def test_http_post_metric(_http, metric):
    """GET /{post_id}/insights?metric={metric}&period=lifetime"""
    if not _http["post_id"]:
        pytest.skip("No post_id available")
    r = _get(
        f"{_http['post_id']}/insights",
        _http["token"],
        {"metric": metric, "period": "lifetime"},
    )
    assert r.status_code == 200, f"metric={metric!r} → HTTP {r.status_code}: {r.text}"
    assert "data" in r.json(), f"metric={metric!r} response missing 'data' key: {r.text}"


# ------------------------------------------------------------------
# 5. Page/videos edge
# ------------------------------------------------------------------


@pytest.mark.integration
def test_http_videos_edge(_http):
    """GET /{page_id}/videos?fields=id,title,description,created_time,length"""
    r = _get(
        f"{_http['page_id']}/videos",
        _http["token"],
        {"fields": "id,title,description,created_time,length"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "data" in data
    assert len(data["data"]) > 0, "Expected at least one video"


# ------------------------------------------------------------------
# 5b. Video/video_insights — per metric
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("metric", VIDEO_METRICS)
def test_http_video_metric(_http, metric):
    """GET /{video_id}/video_insights?metric={metric}&period=lifetime"""
    if not _http["video_id"]:
        pytest.skip("No video_id available")
    r = _get(
        f"{_http['video_id']}/video_insights",
        _http["token"],
        {"metric": metric, "period": "lifetime"},
    )
    assert r.status_code == 200, f"metric={metric!r} → HTTP {r.status_code}: {r.text}"
    assert "data" in r.json(), f"metric={metric!r} response missing 'data' key: {r.text}"
