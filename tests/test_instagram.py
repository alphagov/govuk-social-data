from datetime import date, datetime, timezone

import pyarrow as pa
import pytest

from instagram.models import IGAccount, IGAccountInsightMetric, IGInsightValue, IGMedia, IGMediaInsightMetric
from instagram.schemas import (
    ACCOUNT_INSIGHTS_SCHEMA,
    ACCOUNT_SCHEMA,
    MEDIA_INSIGHTS_SCHEMA,
    MEDIA_SCHEMA,
    account_insights_to_arrow,
    account_to_arrow,
    media_insights_to_arrow,
    media_to_arrow,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

IG_USER_ID = "123456789"
MEDIA_ID = "111111111111111"
TS = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
TARGET_DATE = date(2025, 1, 1)
INGESTION_TS = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_account() -> IGAccount:
    return IGAccount(
        id=IG_USER_ID,
        username="govuk",
        name="GOV.UK",
        biography="Official GOV.UK account",
        followers_count=50000,
        follows_count=100,
        media_count=300,
    )


@pytest.fixture
def sample_account_insights() -> list[IGAccountInsightMetric]:
    return [
        IGAccountInsightMetric(
            name="reach",
            period="day",
            values=[
                IGInsightValue(end_time=TS, value=1000),
                IGInsightValue(end_time=datetime(2025, 1, 3, tzinfo=timezone.utc), value=1200),
            ],
        ),
        IGAccountInsightMetric(
            name="follower_count",
            period="day",
            values=[IGInsightValue(end_time=TS, value=50000)],
        ),
    ]


@pytest.fixture
def sample_media() -> list[IGMedia]:
    return [
        IGMedia(
            id=MEDIA_ID,
            caption="First post",
            media_type="IMAGE",
            media_product_type="FEED",
            permalink="https://www.instagram.com/p/abc/",
            thumbnail_url=None,
            media_url="https://example.com/image.jpg",
            timestamp=TS,
            like_count=42,
            comments_count=5,
        ),
        IGMedia(
            id="222222222222222",
            caption=None,
            media_type="REEL",
            media_product_type="REELS",
            permalink=None,
            thumbnail_url=None,
            media_url=None,
            timestamp=datetime(2025, 1, 3, tzinfo=timezone.utc),
            like_count=None,
            comments_count=None,
        ),
    ]


@pytest.fixture
def sample_media_insights() -> list[IGMediaInsightMetric]:
    return [
        IGMediaInsightMetric(name="reach", period="lifetime", value=500),
        IGMediaInsightMetric(name="likes", period="lifetime", value=42),
        IGMediaInsightMetric(name="views", period="lifetime", value=None),  # None should be skipped
    ]


# ------------------------------------------------------------------
# account_to_arrow
# ------------------------------------------------------------------


def test_account_to_arrow_schema(sample_account):
    table = account_to_arrow(sample_account, IG_USER_ID, TARGET_DATE, INGESTION_TS)
    assert table.schema == pa.schema([(k, ACCOUNT_SCHEMA[k]) for k in ACCOUNT_SCHEMA])


def test_account_to_arrow_values(sample_account):
    table = account_to_arrow(sample_account, IG_USER_ID, TARGET_DATE, INGESTION_TS)
    assert len(table) == 1
    assert table["ig_user_id"][0].as_py() == IG_USER_ID
    assert table["username"][0].as_py() == "govuk"
    assert table["followers_count"][0].as_py() == 50000
    assert table["target_date"][0].as_py() == TARGET_DATE
    assert table["ingestion_ts"][0].as_py() == INGESTION_TS


def test_account_to_arrow_nullable_fields():
    account = IGAccount(id=IG_USER_ID, username="govuk", name=None, biography=None)
    table = account_to_arrow(account, IG_USER_ID, TARGET_DATE, INGESTION_TS)
    assert table["name"][0].as_py() is None
    assert table["biography"][0].as_py() is None


# ------------------------------------------------------------------
# account_insights_to_arrow
# ------------------------------------------------------------------


def test_account_insights_to_arrow_schema(sample_account_insights):
    table = account_insights_to_arrow(IG_USER_ID, sample_account_insights, TARGET_DATE, INGESTION_TS)
    assert table.schema == pa.schema([(k, ACCOUNT_INSIGHTS_SCHEMA[k]) for k in ACCOUNT_INSIGHTS_SCHEMA])


def test_account_insights_to_arrow_row_count(sample_account_insights):
    table = account_insights_to_arrow(IG_USER_ID, sample_account_insights, TARGET_DATE, INGESTION_TS)
    # 2 values for reach + 1 for follower_count = 3 rows
    assert len(table) == 3


def test_account_insights_to_arrow_values(sample_account_insights):
    table = account_insights_to_arrow(IG_USER_ID, sample_account_insights, TARGET_DATE, INGESTION_TS)
    assert table["ig_user_id"][0].as_py() == IG_USER_ID
    assert table["metric"][0].as_py() == "reach"
    assert table["value"][0].as_py() == 1000
    assert table["target_date"][0].as_py() == TARGET_DATE
    assert table["ingestion_ts"][0].as_py() == INGESTION_TS


def test_account_insights_to_arrow_empty():
    table = account_insights_to_arrow(IG_USER_ID, [], TARGET_DATE, INGESTION_TS)
    assert len(table) == 0
    assert table.schema == pa.schema([(k, ACCOUNT_INSIGHTS_SCHEMA[k]) for k in ACCOUNT_INSIGHTS_SCHEMA])


# ------------------------------------------------------------------
# media_to_arrow
# ------------------------------------------------------------------


def test_media_to_arrow_schema(sample_media):
    table = media_to_arrow(IG_USER_ID, sample_media, TARGET_DATE, INGESTION_TS)
    assert table.schema == pa.schema([(k, MEDIA_SCHEMA[k]) for k in MEDIA_SCHEMA])


def test_media_to_arrow_row_count(sample_media):
    table = media_to_arrow(IG_USER_ID, sample_media, TARGET_DATE, INGESTION_TS)
    assert len(table) == 2


def test_media_to_arrow_nullable_fields(sample_media):
    table = media_to_arrow(IG_USER_ID, sample_media, TARGET_DATE, INGESTION_TS)
    assert table["caption"][1].as_py() is None
    assert table["like_count"][1].as_py() is None
    assert table["permalink"][1].as_py() is None


def test_media_to_arrow_values(sample_media):
    table = media_to_arrow(IG_USER_ID, sample_media, TARGET_DATE, INGESTION_TS)
    assert table["media_id"][0].as_py() == MEDIA_ID
    assert table["media_type"][0].as_py() == "IMAGE"
    assert table["like_count"][0].as_py() == 42
    assert table["target_date"][0].as_py() == TARGET_DATE
    assert table["ingestion_ts"][0].as_py() == INGESTION_TS


# ------------------------------------------------------------------
# media_insights_to_arrow
# ------------------------------------------------------------------


def test_media_insights_to_arrow_schema(sample_media_insights):
    table = media_insights_to_arrow(MEDIA_ID, sample_media_insights, TARGET_DATE, INGESTION_TS)
    assert table.schema == pa.schema([(k, MEDIA_INSIGHTS_SCHEMA[k]) for k in MEDIA_INSIGHTS_SCHEMA])


def test_media_insights_to_arrow_skips_none_values(sample_media_insights):
    table = media_insights_to_arrow(MEDIA_ID, sample_media_insights, TARGET_DATE, INGESTION_TS)
    # "views" has value=None and should be excluded
    assert "views" not in table["metric"].to_pylist()


def test_media_insights_to_arrow_row_count(sample_media_insights):
    table = media_insights_to_arrow(MEDIA_ID, sample_media_insights, TARGET_DATE, INGESTION_TS)
    # reach + likes = 2 rows (views skipped)
    assert len(table) == 2


def test_media_insights_to_arrow_values(sample_media_insights):
    table = media_insights_to_arrow(MEDIA_ID, sample_media_insights, TARGET_DATE, INGESTION_TS)
    assert table["media_id"][0].as_py() == MEDIA_ID
    assert table["metric"][0].as_py() == "reach"
    assert table["value"][0].as_py() == 500
    assert table["target_date"][0].as_py() == TARGET_DATE
    assert table["ingestion_ts"][0].as_py() == INGESTION_TS


# ------------------------------------------------------------------
# Integration tests (require live GCP credentials + Instagram access)
# ------------------------------------------------------------------


@pytest.fixture(scope="module")
def _ig():
    """Builds client and fetches shared IDs once for all integration tests."""
    import logging

    logging.disable(logging.CRITICAL)

    from instagram.api import build_client

    client, ig_user_id = build_client()
    media = client.get_media(ig_user_id, max_results=1)
    media_id = media[0].id if media else None
    return {"client": client, "ig_user_id": ig_user_id, "media_id": media_id}


@pytest.fixture(scope="module")
def _account_insight_names(_ig):
    from datetime import date, timedelta

    # follower_count only retains 30 days of data, so use a recent window
    until = date.today() - timedelta(days=1)
    since = until - timedelta(days=6)
    metrics = _ig["client"].get_account_insights(_ig["ig_user_id"], since, until)
    return {m.name for m in metrics}


@pytest.fixture(scope="module")
def _media_insight_names(_ig):
    if not _ig["media_id"]:
        return set()
    insights = _ig["client"].get_media_insights(_ig["media_id"])
    return {m.name for m in insights}


@pytest.mark.integration
def test_integration_build_client():
    from instagram.api import build_client

    client, ig_user_id = build_client()
    assert client is not None
    assert ig_user_id


@pytest.mark.integration
def test_integration_get_account(_ig):
    account = _ig["client"].get_account(_ig["ig_user_id"])
    assert account.id == _ig["ig_user_id"]
    assert account.username


@pytest.mark.integration
def test_integration_get_media(_ig):
    media = _ig["client"].get_media(_ig["ig_user_id"], max_results=5)
    assert len(media) > 0
    table = media_to_arrow(_ig["ig_user_id"], media, TARGET_DATE, INGESTION_TS)
    assert "media_id" in table.column_names


@pytest.mark.integration
def test_integration_get_account_insights_returns_data(_account_insight_names):
    assert len(_account_insight_names) > 0, "get_account_insights() returned no metrics"


@pytest.mark.integration
@pytest.mark.parametrize(
    "metric_name",
    __import__("instagram.api", fromlist=["ACCOUNT_DAY_METRICS"]).ACCOUNT_DAY_METRICS,
)
def test_integration_account_day_metric_present(metric_name, _account_insight_names):
    assert metric_name in _account_insight_names, (
        f"Expected metric '{metric_name}' not returned by get_account_insights(). "
        f"Returned: {sorted(_account_insight_names)}"
    )


@pytest.mark.integration
def test_integration_get_media_insights_returns_data(_ig, _media_insight_names):
    if not _ig["media_id"]:
        pytest.skip("No media available")
    assert len(_media_insight_names) > 0, "get_media_insights() returned no metrics"


@pytest.mark.integration
@pytest.mark.parametrize(
    "metric_name",
    __import__("instagram.api", fromlist=["MEDIA_LIFETIME_METRICS"]).MEDIA_LIFETIME_METRICS,
)
def test_integration_media_insight_metric_present(metric_name, _ig, _media_insight_names):
    if not _ig["media_id"]:
        pytest.skip("No media available")
    assert metric_name in _media_insight_names, (
        f"Expected metric '{metric_name}' not returned by get_media_insights(). "
        f"Returned: {sorted(_media_insight_names)}"
    )


@pytest.mark.integration
def test_integration_account_insights_arrow(_ig):
    from datetime import date, timedelta

    until = date.today() - timedelta(days=1)
    since = until - timedelta(days=6)
    metrics = _ig["client"].get_account_insights(_ig["ig_user_id"], since, until)
    table = account_insights_to_arrow(_ig["ig_user_id"], metrics, TARGET_DATE, INGESTION_TS)
    assert len(table) > 0
    assert "metric" in table.column_names


@pytest.mark.integration
def test_integration_media_arrow(_ig):
    media = _ig["client"].get_media(_ig["ig_user_id"], max_results=5)
    table = media_to_arrow(_ig["ig_user_id"], media, TARGET_DATE, INGESTION_TS)
    assert len(table) > 0
    assert "media_id" in table.column_names
