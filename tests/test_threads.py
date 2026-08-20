"""Unit and integration tests for the Threads pipeline.

Unit tests need no credentials and make no network calls. Integration tests are marked and hit the
live Graph API:

    poetry run pytest tests/test_threads.py -m "not integration" -v
    poetry run pytest tests/test_threads.py -m integration -v
"""

from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pytest
import requests

from tests.helpers import http_error, make_fake_get, paging, stub_pages
from threads.api import (
    ACCOUNT_LIFETIME_METRICS,
    ACCOUNT_TIME_SERIES_METRICS,
    ACCOUNT_TOTAL_VALUE_METRICS,
    DEMOGRAPHIC_BREAKDOWNS,
    INSIGHTS_RETENTION_DAYS,
    MEDIA_METRICS,
    InsightMetricError,
    PaginationError,
    ThreadsClient,
    _clip_to_window,
    _parse_demographics,
    _parse_insight_metric,
    _to_unix,
    _window_params,
)
from threads.models import (
    ThreadsAccountInsightMetric,
    ThreadsDemographicRow,
    ThreadsInsightValue,
    ThreadsMedia,
    ThreadsMediaInsightMetric,
    ThreadsProfile,
    ThreadsReply,
)
from threads.schemas import (
    ACCOUNT_INSIGHTS_SCHEMA,
    ACCOUNT_LIFETIME_SCHEMA,
    FOLLOWER_DEMOGRAPHICS_SCHEMA,
    MEDIA_INSIGHTS_SCHEMA,
    MEDIA_SCHEMA,
    PROFILE_SCHEMA,
    REPLIES_SCHEMA,
    account_insights_to_arrow,
    account_lifetime_to_arrow,
    follower_demographics_to_arrow,
    media_insights_to_arrow,
    media_to_arrow,
    profile_to_arrow,
    replies_to_arrow,
)

USER_ID = "27469101309343572"
MEDIA_ID = "18113985254286218"
REPLY_ID = "18000000000000001"
TS = "2026-08-18T09:30:00+0000"

ACCOUNT_WINDOW_METRICS = ACCOUNT_TIME_SERIES_METRICS + ACCOUNT_TOTAL_VALUE_METRICS

# One fully-populated post and one with everything the API can omit left absent.
MEDIA = [
    ThreadsMedia.model_validate(
        {
            "id": MEDIA_ID,
            "media_product_type": "THREADS",
            "media_type": "TEXT_POST",
            "permalink": "https://www.threads.net/@govuk/post/abc",
            "text": "Check your energy certificate",
            "timestamp": TS,
            "shortcode": "abc",
            "is_quote_post": False,
            "has_replies": True,
            "poll_attachment": {"option_a": "True", "option_b": "False"},
            "topic_tag": "ALevelResultsDay",
        }
    ),
    ThreadsMedia(id="18000000000000002"),
]

MEDIA_WITH_INSIGHTS = [
    MEDIA[0].model_copy(
        update={
            "insights": [
                ThreadsMediaInsightMetric(name="views", period="lifetime", value=1850),
                ThreadsMediaInsightMetric(name="clicks", period="lifetime", value=0),
                # Accepted by the API but with no value; the converter drops it.
                ThreadsMediaInsightMetric(name="shares", period="lifetime", value=None),
            ]
        }
    ),
    MEDIA[1],
]

REPLIES = [
    ThreadsReply.model_validate(
        {
            "id": REPLY_ID,
            "media_type": "TEXT_POST",
            "text": "Thanks for asking",
            "timestamp": TS,
            "has_replies": False,
            "is_reply": True,
            "root_post": {"id": MEDIA_ID},
            "replied_to": {"id": MEDIA_ID},
            "hide_status": "NOT_HUSHED",
        }
    ),
    ThreadsReply(id="18000000000000003"),  # a root post of its own — neither object present
]


def _schema_of(schema: dict[str, pa.DataType]) -> pa.Schema:
    return pa.schema([(k, schema[k]) for k in schema])


def _nested_demographics(breakdown: str, results: list[tuple[str, int]]) -> dict:
    """The shape the live API returns, as captured in research_document.md."""
    return {
        "name": "follower_demographics",
        "total_value": {
            "breakdowns": [
                {
                    "dimension_keys": [breakdown],
                    "results": [{"dimension_values": [v], "value": n} for v, n in results],
                }
            ]
        },
    }


# ------------------------------------------------------------------
# Converters
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "table,schema",
    [
        (profile_to_arrow(ThreadsProfile(id=USER_ID), USER_ID), PROFILE_SCHEMA),
        (media_to_arrow(USER_ID, []), MEDIA_SCHEMA),
        (media_insights_to_arrow([]), MEDIA_INSIGHTS_SCHEMA),
        (replies_to_arrow(USER_ID, []), REPLIES_SCHEMA),
        (account_insights_to_arrow(USER_ID, []), ACCOUNT_INSIGHTS_SCHEMA),
        (account_lifetime_to_arrow(USER_ID, []), ACCOUNT_LIFETIME_SCHEMA),
        (follower_demographics_to_arrow(USER_ID, []), FOLLOWER_DEMOGRAPHICS_SCHEMA),
    ],
)
def test_converters_type_their_columns_with_no_input(table, schema):
    """An empty input must still yield a correctly typed table, not a null-typed one."""
    assert table.schema == _schema_of(schema)


def test_profile_to_arrow():
    profile = ThreadsProfile(id=USER_ID, username="govuk", threads_biography="Official", is_verified=True)
    row = profile_to_arrow(profile, USER_ID).to_pylist()[0]
    # API field names are renamed on the way into BigQuery.
    assert row == {
        "threads_user_id": USER_ID,
        "username": "govuk",
        "name": None,
        "biography": "Official",
        "profile_picture_url": None,
        "is_verified": True,
    }


def test_media_to_arrow():
    rows = media_to_arrow(USER_ID, MEDIA).to_pylist()
    assert len(rows) == 2  # one row per post, however sparse
    assert rows[0]["media_id"] == MEDIA_ID
    assert rows[0]["is_quote_post"] is False and rows[0]["has_replies"] is True
    assert rows[0]["timestamp"] == datetime(2026, 8, 18, 9, 30)
    assert rows[1]["media_type"] is None and rows[1]["timestamp"] is None


@pytest.mark.parametrize(
    "field,value,expected",
    [
        # A dict is JSON-encoded; a bare string is passed through, since encoding it would bury
        # the tag inside quotes. topic_tag arrives as a string live and an object in the docs.
        ("poll_attachment", {"option_a": "True"}, '{"option_a": "True"}'),
        ("topic_tag", "ALevelResultsDay", "ALevelResultsDay"),
        ("topic_tag", {"id": "9", "name": "Politics"}, '{"id": "9", "name": "Politics"}'),
        ("topic_tag", None, None),
    ],
)
def test_media_to_arrow_nested_fields(field, value, expected):
    media = ThreadsMedia.model_validate({"id": MEDIA_ID, field: value})
    assert media_to_arrow(USER_ID, [media]).to_pylist()[0][field] == expected


def test_media_insights_to_arrow():
    rows = media_insights_to_arrow(MEDIA_WITH_INSIGHTS).to_pylist()
    # 3 metrics on post one, of which shares has no value; post two has no insights at all.
    assert rows == [
        {"media_id": MEDIA_ID, "metric": "views", "period": "lifetime", "value": 1850},
        # 0 is data, not absence — clicks reads 0 on every post so far and must still land.
        {"media_id": MEDIA_ID, "metric": "clicks", "period": "lifetime", "value": 0},
    ]


def test_replies_to_arrow():
    rows = replies_to_arrow(USER_ID, REPLIES).to_pylist()
    assert rows[0]["reply_id"] == REPLY_ID
    assert rows[0]["root_post_id"] == MEDIA_ID and rows[0]["replied_to_id"] == MEDIA_ID
    # A reply that is itself a root post lands NULL rather than raising.
    assert rows[1]["root_post_id"] is None and rows[1]["replied_to_id"] is None


def test_account_insights_to_arrow():
    metrics = [
        ThreadsAccountInsightMetric(
            name="views", period="day", values=[ThreadsInsightValue(end_time=TS, value=5460.0)]
        ),
        ThreadsAccountInsightMetric(name="likes", period="day", values=[ThreadsInsightValue(value=6)]),
    ]
    rows = account_insights_to_arrow(USER_ID, metrics).to_pylist()
    assert rows[0]["end_time"] == datetime(2026, 8, 18, 9, 30)
    assert rows[0]["value"] == 5460  # float counts are coerced to int64
    # total_value metrics describe the window, not a day within it, so they carry no end_time.
    assert rows[1]["metric"] == "likes" and rows[1]["end_time"] is None


def test_account_lifetime_to_arrow_drops_period_and_end_time():
    metrics = [ThreadsAccountInsightMetric(name="followers_count", values=[ThreadsInsightValue(value=87177)])]
    assert account_lifetime_to_arrow(USER_ID, metrics).to_pylist() == [
        {"threads_user_id": USER_ID, "metric": "followers_count", "value": 87177}
    ]


def test_follower_demographics_to_arrow():
    rows = [
        ThreadsDemographicRow(breakdown="country", dimension_value="GB", value=78000),
        ThreadsDemographicRow(breakdown="city", dimension_value="London", value=None),  # dropped
    ]
    assert follower_demographics_to_arrow(USER_ID, rows).to_pylist() == [
        {"threads_user_id": USER_ID, "breakdown": "country", "dimension_value": "GB", "value": 78000}
    ]


# ------------------------------------------------------------------
# Parsers
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "item,expected_values",
    [
        # values[] — the time-series shape
        ({"name": "views", "period": "day", "values": [{"value": 5460}, {"value": 5476}]}, [5460, 5476]),
        # total_value — normalised into a one-entry values list, which keeps the converters a plain loop
        ({"name": "likes", "period": "day", "total_value": {"value": 6}}, [6]),
        # lifetime: same shape, no period
        ({"name": "followers_count", "total_value": {"value": 87177}}, [87177]),
        # non-numeric and absent are both "no data" rather than errors
        ({"name": "views", "values": [{"value": None}]}, []),
        ({"name": "views", "period": "day"}, []),
    ],
)
def test_parse_insight_metric(item, expected_values):
    assert [v.value for v in _parse_insight_metric(item).values] == expected_values


@pytest.mark.parametrize(
    "item,expected",
    [
        # The shape the live API returns.
        (_nested_demographics("age", [("13-17", 1329), ("25-34", 19220)]), [("13-17", 1329), ("25-34", 19220)]),
        # The flatter shape Meta's docs show; the parser falls back to it.
        ({"breakdown": [{"country": "US", "value": 900}]}, [("US", 900)]),
        # Below 100 followers the edge answers {} rather than a 400 — it must not raise.
        ({}, []),
        ({"total_value": {}}, []),
    ],
)
def test_parse_demographics(item, expected):
    rows = _parse_demographics(item, "age")
    assert [(r.dimension_value, r.value) for r in rows] == expected


def test_to_unix_is_midnight_utc():
    assert datetime.fromtimestamp(_to_unix(date(2026, 8, 19)), tz=timezone.utc).hour == 0


def test_to_unix_retention_boundary():
    """The oldest retained day is allowed; the day before it fails fast with a readable message
    rather than an opaque Graph 400 mid-run."""
    assert _to_unix(date.today() - timedelta(days=INSIGHTS_RETENTION_DAYS - 1)) > 0

    too_old = date.today() - timedelta(days=INSIGHTS_RETENTION_DAYS)
    with pytest.raises(ValueError) as exc_info:
        _to_unix(too_old)
    assert too_old.isoformat() in str(exc_info.value)


def test_window_params_upper_bound_is_inclusive():
    target = date(2026, 8, 19)
    params = _window_params(target, target)
    assert params["since"] == _to_unix(target)
    assert params["until"] - params["since"] == 86400


@pytest.mark.parametrize(
    "end_times,expected",
    [
        # threads_insights answers a one-day window with the requested day AND the day before.
        # Keeping the extra would put the same (metric, end_time) in two target_date partitions,
        # which per-partition WRITE_TRUNCATE cannot dedupe.
        (["2026-08-17T07:00:00+0000", "2026-08-18T07:00:00+0000"], ["2026-08-18"]),
        (["2026-08-18T07:00:00+0000", "2026-08-19T07:00:00+0000"], ["2026-08-18"]),
        ([], []),
    ],
)
def test_clip_to_window_keeps_only_the_target_date(end_times, expected):
    metrics = [
        ThreadsAccountInsightMetric(
            name="views",
            period="day",
            values=[ThreadsInsightValue(end_time=t, value=1) for t in end_times],
        )
    ]
    target = date(2026, 8, 18)
    clipped = _clip_to_window(metrics, target, target)
    assert [v.end_time.date().isoformat() for v in clipped[0].values] == expected


def test_clip_to_window_keeps_values_without_an_end_time():
    # total_value metrics describe the whole window, so there is no bucket to place them in.
    metrics = [ThreadsAccountInsightMetric(name="likes", values=[ThreadsInsightValue(value=6)])]
    clipped = _clip_to_window(metrics, date(2026, 8, 18), date(2026, 8, 18))
    assert clipped[0].values[0].value == 6


# ------------------------------------------------------------------
# Failure handling
# ------------------------------------------------------------------


def test_get_media_insights_partial_result_on_metric_failure(monkeypatch):
    client = ThreadsClient(access_token="test-token")
    monkeypatch.setattr(client, "_get", make_fake_get(MEDIA_METRICS, "shares"))

    with pytest.raises(InsightMetricError) as exc_info:
        client.get_media_insights(ThreadsMedia(id=MEDIA_ID))

    err = exc_info.value
    assert err.metrics == ["shares"]
    assert {i.name for i in err.partial_result.insights} == set(MEDIA_METRICS) - {"shares"}


def test_get_account_insights_partial_result_on_metric_failure(monkeypatch):
    client = ThreadsClient(access_token="test-token")
    monkeypatch.setattr(client, "_get", make_fake_get(ACCOUNT_WINDOW_METRICS, "quotes"))
    target = date.today() - timedelta(days=1)

    with pytest.raises(InsightMetricError) as exc_info:
        client.get_account_insights(USER_ID, target, target)

    err = exc_info.value
    assert err.metrics == ["quotes"]
    # Already parsed into models, so main.py hands partial_result straight to the converter.
    assert {m.name for m in err.partial_result} == set(ACCOUNT_WINDOW_METRICS) - {"quotes"}


def test_get_follower_demographics_skips_a_failing_breakdown(monkeypatch):
    """One bad breakdown must not take the other three down with it."""
    client = ThreadsClient(access_token="test-token")

    def fake_get(path, params=None, retries=3):
        if params["breakdown"] == "city":
            raise requests.ConnectionError("boom")
        return {"data": [_nested_demographics(params["breakdown"], [("GB", 78000)])]}

    monkeypatch.setattr(client, "_get", fake_get)

    rows = client.get_follower_demographics(USER_ID)

    assert {r.breakdown for r in rows} == set(DEMOGRAPHIC_BREAKDOWNS) - {"city"}


def test_get_profile_does_not_swallow_errors(monkeypatch):
    """An auth failure must stay loud — it is not a recoverable per-metric degradation."""
    client = ThreadsClient(access_token="test-token")

    def fake_get(path, params=None, retries=3):
        raise http_error(401)

    monkeypatch.setattr(client, "_get", fake_get)

    with pytest.raises(requests.HTTPError):
        client.get_profile(USER_ID)


def test_get_media_walks_pages_by_cursor(monkeypatch):
    client = ThreadsClient(access_token="test-token")
    calls = stub_pages(
        monkeypatch,
        client,
        [
            {"data": [{"id": "a"}], "paging": paging(after="CURSOR", next_url="https://x/next")},
            {"data": [{"id": "b"}], "paging": {}},
        ],
    )

    assert [m.id for m in client.get_media(USER_ID)] == ["a", "b"]
    # Page two re-requests our own path with the cursor, so our fields/limit keep applying.
    assert calls[1][0] == f"{USER_ID}/threads"
    assert calls[1][1]["after"] == "CURSOR"


@pytest.mark.parametrize("fetch,path", [("get_media", "threads"), ("get_replies", "replies")])
def test_pagination_failure_keeps_the_pages_that_worked(monkeypatch, fetch, path):
    client = ThreadsClient(access_token="test-token")

    def fake_get(_path, params=None, retries=3):
        if params and "after" in params:
            raise requests.ConnectionError("boom")
        return {"data": [{"id": "a"}, {"id": "b"}], "paging": paging(after="CURSOR", next_url="https://x/next")}

    monkeypatch.setattr(client, "_get", fake_get)

    with pytest.raises(PaginationError) as exc_info:
        getattr(client, fetch)(USER_ID)

    # Page one survives as fully-built models, not raw dicts.
    assert [item.id for item in exc_info.value.partial_result] == ["a", "b"]


# ------------------------------------------------------------------
# Integration tests (require live GCP credentials + Threads access)
# ------------------------------------------------------------------


@pytest.fixture(scope="module")
def _th():
    """Builds the client and grabs one media id once for all integration tests."""
    import logging

    logging.disable(logging.CRITICAL)

    from threads.api import build_client

    client, threads_user_id = build_client()
    # _get rather than _paginate: one page is all this needs, and a pagination fault here should
    # fail the pagination tests, not every test in the module.
    data = client._get(f"{threads_user_id}/threads", params={"fields": "id", "limit": 1}).get("data", [])
    media = ThreadsMedia.model_validate(data[0]) if data else None
    return {"client": client, "threads_user_id": threads_user_id, "media": media}


@pytest.fixture(scope="module")
def _account_insight_names(_th):
    target = date.today() - timedelta(days=1)
    return {m.name for m in _th["client"].get_account_insights(_th["threads_user_id"], target, target)}


@pytest.fixture(scope="module")
def _media_insight_names(_th):
    if not _th["media"]:
        return set()
    return {m.name for m in _th["client"].get_media_insights(_th["media"]).insights}


@pytest.mark.integration
def test_integration_get_profile(_th):
    profile = _th["client"].get_profile(_th["threads_user_id"])
    assert profile.id == _th["threads_user_id"] and profile.username
    assert profile_to_arrow(profile, _th["threads_user_id"]).num_rows == 1


@pytest.mark.integration
def test_integration_get_media(_th):
    media = _th["client"].get_media(_th["threads_user_id"], max_results=5)
    assert len(media) > 0
    assert media_to_arrow(_th["threads_user_id"], media).schema == _schema_of(MEDIA_SCHEMA)


@pytest.mark.integration
def test_integration_get_replies(_th):
    replies = _th["client"].get_replies(_th["threads_user_id"], max_results=5)
    assert replies_to_arrow(_th["threads_user_id"], replies).schema == _schema_of(REPLIES_SCHEMA)


@pytest.mark.integration
@pytest.mark.parametrize("metric", ACCOUNT_WINDOW_METRICS)
def test_integration_account_insight_metric(_account_insight_names, metric):
    assert metric in _account_insight_names, f"{metric!r} missing; got {sorted(_account_insight_names)}"


@pytest.mark.integration
@pytest.mark.parametrize("metric", ACCOUNT_LIFETIME_METRICS)
def test_integration_account_lifetime_metric(_th, metric):
    metrics = _th["client"].get_account_lifetime_insights(_th["threads_user_id"])
    names = {m.name for m in metrics}
    assert metric in names, f"{metric!r} missing; got {sorted(names)}"
    assert account_lifetime_to_arrow(_th["threads_user_id"], metrics).num_rows > 0


@pytest.mark.integration
@pytest.mark.parametrize("metric", MEDIA_METRICS)
def test_integration_media_insight_metric(_media_insight_names, metric):
    assert metric in _media_insight_names, f"{metric!r} missing; got {sorted(_media_insight_names)}"


@pytest.mark.integration
@pytest.mark.parametrize("breakdown", DEMOGRAPHIC_BREAKDOWNS)
def test_integration_follower_demographic_breakdown(_th, breakdown):
    rows = _th["client"].get_follower_demographics(_th["threads_user_id"], breakdowns=[breakdown])
    assert rows, f"{breakdown!r} returned no rows"
    assert follower_demographics_to_arrow(_th["threads_user_id"], rows).num_rows == len(rows)


@pytest.mark.integration
def test_integration_account_insights_are_clipped_to_the_target_date(_th):
    """The daily partition must hold the target date only, never the day before it."""
    target = date.today() - timedelta(days=1)
    metrics = _th["client"].get_account_insights(_th["threads_user_id"], target, target)
    dated = [v.end_time.date() for m in metrics for v in m.values if v.end_time is not None]
    assert dated, "no dated buckets returned — clipping may have dropped everything"
    assert set(dated) == {target}, f"expected only {target}, got {sorted(set(dated))}"
