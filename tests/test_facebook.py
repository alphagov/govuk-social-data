from datetime import datetime, timezone

import pyarrow as pa
import pytest


from facebook.api import PAGE_DAY_METRICS, PAGE_LIFETIME_METRICS, POST_METRICS, VIDEO_METRICS
from facebook.models import FacebookReelInsights, Page, PageInsightMetric, PageInsightValue, Post, PostInsightMetric
from facebook.schemas import (
    PAGE_INFO_SCHEMA,
    PAGE_INSIGHTS_SCHEMA,
    POST_INSIGHTS_SCHEMA,
    POST_REACTIONS_SCHEMA,
    POSTS_SCHEMA,
    page_info_to_arrow,
    page_insights_to_arrow,
    post_insights_to_arrow,
    post_reactions_to_arrow,
    posts_to_arrow,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

PAGE_ID = "123456789"
POST_ID = "123456789_987654321"
TS = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_page() -> Page:
    return Page(
        page_id=PAGE_ID,
        name="Test Page",
        fan_count=1000,
        followers_count=950,
        talking_about_count=42,
    )


@pytest.fixture
def sample_page_insights() -> list[PageInsightMetric]:
    return [
        PageInsightMetric(
            name="page_impressions",
            period="day",
            values=[
                PageInsightValue(end_time=TS, value=500),
                PageInsightValue(end_time=datetime(2024, 6, 2, 12, 0, 0, tzinfo=timezone.utc), value=600),
            ],
        ),
        PageInsightMetric(
            name="page_fans",
            period="lifetime",
            values=[PageInsightValue(end_time=TS, value=1000)],
        ),
    ]


@pytest.fixture
def sample_posts() -> list[Post]:
    return [
        Post(
            post_id=POST_ID,
            page_id=PAGE_ID,
            message="Hello world",
            created_time=TS,
            post_type="photo",
            permalink_url="https://www.facebook.com/permalink/1",
        ),
        Post(
            post_id="123456789_111111111",
            page_id=PAGE_ID,
            message=None,
            created_time=datetime(2024, 6, 2, 10, 0, 0, tzinfo=timezone.utc),
            post_type="video",
            permalink_url=None,
        ),
    ]


@pytest.fixture
def sample_posts_with_insights(sample_posts) -> list[Post]:
    posts = list(sample_posts)
    posts[0] = posts[0].model_copy(
        update={
            "insights": [
                PostInsightMetric(name="post_impressions", period="lifetime", value=300),
                PostInsightMetric(name="post_engaged_users", period="lifetime", value=50),
                PostInsightMetric(
                    name="post_reactions_by_type_total",
                    period="lifetime",
                    value={"LIKE": 40, "LOVE": 5, "WOW": 2, "HAHA": 1, "SORRY": 0, "ANGER": 0},
                ),
            ]
        }
    )
    return posts


# ------------------------------------------------------------------
# page_info_to_arrow
# ------------------------------------------------------------------


def test_page_info_to_arrow_schema(sample_page):
    table = page_info_to_arrow(sample_page)
    assert table.schema == pa.schema([(k, PAGE_INFO_SCHEMA[k]) for k in PAGE_INFO_SCHEMA])


def test_page_info_to_arrow_values(sample_page):
    table = page_info_to_arrow(sample_page)
    assert table["page_id"][0].as_py() == PAGE_ID
    assert table["name"][0].as_py() == "Test Page"
    assert table["fan_count"][0].as_py() == 1000
    assert len(table) == 1


# ------------------------------------------------------------------
# page_insights_to_arrow
# ------------------------------------------------------------------


def test_page_insights_to_arrow_schema(sample_page_insights):
    table = page_insights_to_arrow(PAGE_ID, sample_page_insights)
    assert table.schema == pa.schema([(k, PAGE_INSIGHTS_SCHEMA[k]) for k in PAGE_INSIGHTS_SCHEMA])


def test_page_insights_to_arrow_row_count(sample_page_insights):
    table = page_insights_to_arrow(PAGE_ID, sample_page_insights)
    # 2 values for page_impressions + 1 for page_fans = 3 rows
    assert len(table) == 3


def test_page_insights_to_arrow_values(sample_page_insights):
    table = page_insights_to_arrow(PAGE_ID, sample_page_insights)
    assert table["page_id"][0].as_py() == PAGE_ID
    assert table["metric"][0].as_py() == "page_impressions"
    assert table["value"][0].as_py() == 500


# ------------------------------------------------------------------
# posts_to_arrow
# ------------------------------------------------------------------


def test_posts_to_arrow_schema(sample_posts):
    table = posts_to_arrow(sample_posts)
    assert table.schema == pa.schema([(k, POSTS_SCHEMA[k]) for k in POSTS_SCHEMA])


def test_posts_to_arrow_row_count(sample_posts):
    table = posts_to_arrow(sample_posts)
    assert len(table) == 2


def test_posts_to_arrow_nullable_fields(sample_posts):
    table = posts_to_arrow(sample_posts)
    assert table["message"][1].as_py() is None
    assert table["permalink_url"][1].as_py() is None


# ------------------------------------------------------------------
# post_insights_to_arrow
# ------------------------------------------------------------------


def test_post_insights_to_arrow_schema(sample_posts_with_insights):
    table = post_insights_to_arrow(sample_posts_with_insights)
    assert table.schema == pa.schema([(k, POST_INSIGHTS_SCHEMA[k]) for k in POST_INSIGHTS_SCHEMA])


def test_post_insights_to_arrow_skips_dict_values(sample_posts_with_insights):
    table = post_insights_to_arrow(sample_posts_with_insights)
    # post_reactions_by_type_total has a dict value — must be excluded from this table
    assert "post_reactions_by_type_total" not in table["metric"].to_pylist()


def test_post_insights_to_arrow_row_count(sample_posts_with_insights):
    table = post_insights_to_arrow(sample_posts_with_insights)
    # 2 scalar insights on post[0], post[1] has no insights
    assert len(table) == 2


# ------------------------------------------------------------------
# post_reactions_to_arrow
# ------------------------------------------------------------------


def test_post_reactions_to_arrow_schema(sample_posts_with_insights):
    table = post_reactions_to_arrow(sample_posts_with_insights)
    assert table.schema == pa.schema([(k, POST_REACTIONS_SCHEMA[k]) for k in POST_REACTIONS_SCHEMA])


def test_post_reactions_to_arrow_values(sample_posts_with_insights):
    table = post_reactions_to_arrow(sample_posts_with_insights)
    assert len(table) == 1
    assert table["post_id"][0].as_py() == POST_ID
    assert table["like"][0].as_py() == 40
    assert table["love"][0].as_py() == 5
    assert table["anger"][0].as_py() == 0


def test_post_reactions_to_arrow_missing_reactions_skipped(sample_posts):
    # Posts with no reaction insight should produce an empty table
    table = post_reactions_to_arrow(sample_posts)
    assert len(table) == 0


# ------------------------------------------------------------------
# Integration tests (require live GCP credentials + Facebook access)
# ------------------------------------------------------------------


# Module-scoped fixtures — API called once per test session, shared across all integration tests.


@pytest.fixture(scope="module")
def _fb():
    """Builds client and fetches shared IDs once for all integration tests in this module."""
    import logging

    logging.disable(logging.CRITICAL)
    from facebook.api import PAGE_ID_SECRET, PROJECT_ID, build_client
    from src.authentication import get_gcp_secret

    client = build_client()
    page_id = get_gcp_secret(PAGE_ID_SECRET, project_id=PROJECT_ID)
    # Use _get (not _paginate) — we only need the first item, not all pages
    posts_data = client._get(f"{page_id}/posts", params={"fields": "id", "limit": "1"})
    post_id = posts_data["data"][0]["id"] if posts_data.get("data") else None
    videos_data = client._get(f"{page_id}/videos", params={"fields": "id", "limit": "1"})
    video_id = videos_data["data"][0]["id"] if videos_data.get("data") else None
    return {"client": client, "page_id": page_id, "post_id": post_id, "video_id": video_id}


@pytest.fixture(scope="module")
def _page_insight_names(_fb):
    """Calls get_page_insights() once; returns the set of metric names returned."""
    metrics = _fb["client"].get_page_insights(_fb["page_id"], since="2025-01-01", until="2025-01-07")
    return {m.name for m in metrics}


@pytest.fixture(scope="module")
def _post_with_insights(_fb):
    """Calls get_post_insights() on the most recent post; returns the populated Post."""
    if not _fb["post_id"]:
        pytest.skip("No posts available on page")
    from datetime import timezone

    from facebook.models import Post

    stub = Post(
        post_id=_fb["post_id"],
        page_id=_fb["page_id"],
        created_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        post_type="unknown",
    )
    return _fb["client"].get_post_insights(stub)


@pytest.fixture(scope="module")
def _post_insight_names(_post_with_insights):
    """Returns the set of metric names returned by get_post_insights()."""
    return {m.name for m in _post_with_insights.insights}


@pytest.fixture(scope="module")
def _first_video_with_insights(_fb):
    """Returns the first video with populated insights from the most recent 10 videos.

    Very recently published videos may return empty insight data from Facebook, so we
    iterate until we find one that has at least one populated metric field.
    """
    videos = _fb["client"].get_videos(_fb["page_id"], max_results=10)
    if not videos:
        pytest.skip("No videos available on page")
    for video in videos:
        result = _fb["client"].get_video_insights(video)
        insights = result.insights
        if insights is not None and any(getattr(insights, f) is not None for f in FacebookReelInsights.model_fields):
            return result
    pytest.skip("No video with populated insights found in the most recent 10 videos")

# ------------------------------------------------------------------
# Basic client / node / edge tests
# ------------------------------------------------------------------


@pytest.mark.integration
def test_integration_build_client():
    from facebook.api import build_client

    client = build_client()
    assert client is not None


@pytest.mark.integration
def test_integration_get_page(_fb):
    page = _fb["client"].get_page(_fb["page_id"])
    assert page.page_id == _fb["page_id"]
    assert page.name


@pytest.mark.integration
def test_integration_get_posts(_fb):
    posts = _fb["client"].get_posts(_fb["page_id"], limit=5, max_results=5)
    assert len(posts) > 0
    table = posts_to_arrow(posts)
    assert "post_id" in table.column_names


@pytest.mark.integration
    videos = _fb["client"].get_videos(_fb["page_id"], limit=5, max_results=5)
    assert len(videos) > 0
    from facebook.schemas import videos_to_arrow

    table = videos_to_arrow(videos)
    assert "video_id" in table.column_names


# ------------------------------------------------------------------
# Page insights — one call, check each expected metric is present
# ------------------------------------------------------------------


@pytest.mark.integration
def test_integration_page_insights_returns_data(_page_insight_names):
    assert len(_page_insight_names) > 0, "get_page_insights() returned no metrics"


@pytest.mark.integration
@pytest.mark.parametrize(
    "metric_name",
    PAGE_DAY_METRICS + PAGE_LIFETIME_METRICS,
)
def test_integration_page_insight_metric_present(metric_name, _page_insight_names):
    assert metric_name in _page_insight_names, (
        f"Expected metric '{metric_name}' not returned by get_page_insights(). "
        f"Returned: {sorted(_page_insight_names)}"
    )


# ------------------------------------------------------------------
# Post insights — one call, check each expected metric is present
# ------------------------------------------------------------------


@pytest.mark.integration
def test_integration_post_insights_returns_data(_post_insight_names):
    assert len(_post_insight_names) > 0, "get_post_insights() returned no metrics"


@pytest.mark.integration
@pytest.mark.parametrize(
    "metric_name",
    POST_METRICS,
)
def test_integration_post_insight_metric_present(metric_name, _post_insight_names):
    assert metric_name in _post_insight_names, (
        f"Expected metric '{metric_name}' not returned by get_post_insights(). "
        f"Returned: {sorted(_post_insight_names)}"
    )


# ------------------------------------------------------------------
# Video insights
# ------------------------------------------------------------------


@pytest.mark.integration
def test_integration_video_insights_returns_data(_first_video_with_insights):
    insights = _first_video_with_insights.insights
    assert insights is not None, "get_video_insights() returned no metrics"
    populated = [f for f in FacebookReelInsights.model_fields if getattr(insights, f) is not None]
    assert len(populated) > 0, "get_video_insights() returned no metrics"


@pytest.mark.integration
@pytest.mark.parametrize(
    "metric_name",
    VIDEO_METRICS,
)
def test_integration_video_insight_metric_present(metric_name, _first_video_with_insights):
    insights = _first_video_with_insights.insights
    returned = {f for f in FacebookReelInsights.model_fields if getattr(insights, f) is not None}
    assert metric_name in returned, (
        f"Expected metric '{metric_name}' not returned by get_video_insights(). " f"Returned: {sorted(returned)}"
    )


# ------------------------------------------------------------------
# Schema round-trip — verify Arrow conversion still works with live data
# ------------------------------------------------------------------


@pytest.mark.integration
def test_integration_page_insights_arrow(_fb):
    metrics = _fb["client"].get_page_insights(_fb["page_id"], since="2025-01-01", until="2025-01-07")
    table = page_insights_to_arrow(_fb["page_id"], metrics)
    assert len(table) > 0
    assert "metric" in table.column_names


@pytest.mark.integration
def test_integration_post_insights_arrow(_post_with_insights):
    from facebook.schemas import post_insights_to_arrow

    table = post_insights_to_arrow([_post_with_insights])
    assert "metric" in table.column_names
