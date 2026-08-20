"""Threads Graph API client.

Transport (_get, _paginate, _next_page, _fetch_insight_items) is inherited from
src.meta.client.GraphClient; this module supplies the base URL, the metric constants, the model
builders and the response parsers.

Threads client - (user Node)
|
|-- get_media                        _paginate              _get
|-- get_replies                      _paginate              _get
|-- get_profile                                             _get
|-- get_media_insights               _fetch_insight_items   _get
|-- get_account_insights             _fetch_insight_items   _get
|-- get_account_lifetime_insights    _fetch_insight_items   _get
|-- get_follower_demographics                               _get

Node    Edge                Node        Edge
- User
        - threads           -Media      - insights
        - replies
        - threads_insights

Ref: https://developers.facebook.com/docs/threads
"""

import logging
from datetime import date, datetime, timedelta, timezone

import requests

from src.authentication import get_gcp_secret
from src.meta.auth import get_graph_token
from src.meta.client import GraphClient

# Re-exported: threads/main.py and the tests import these from here, as facebook/api.py does.
from src.meta.errors import InsightMetricError, PaginationError  # noqa: F401
from threads.models import (
    ThreadsAccountInsightMetric,
    ThreadsDemographicRow,
    ThreadsInsightValue,
    ThreadsMedia,
    ThreadsMediaInsightMetric,
    ThreadsProfile,
    ThreadsReply,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://graph.threads.net/v1.0"

PROJECT_ID = "gds-social-data"
TOKEN_SECRET = "threads_long_lived_access_token"
USER_ID_SECRET = "threads_user_id"

# User-insight retention is a rolling window, not the fixed 2024-04-13 launch date the docs imply:
# "since param is not valid. Metrics data is available for the last 2 years". If out of bounds
#  returns an out-of-range date up front so a bad --target-date gives a readable error, not an opaque 400.
INSIGHTS_RETENTION_DAYS = 730

# Field lists are a literal transcription of the models in threads/models.py.
PROFILE_FIELDS = "id,username,name,threads_profile_picture_url,threads_biography,is_verified"
MEDIA_FIELDS = (
    "id,media_product_type,media_type,permalink,text,timestamp,shortcode,"
    "is_quote_post,has_replies,link_attachment_url,gif_url,alt_text,poll_attachment,topic_tag"
)
REPLY_FIELDS = "id,media_type,text,timestamp,shortcode,permalink,has_replies,is_reply,root_post,replied_to,hide_status"

# Update these lists if the HTTP tests in tests/test_threads_http.py reveal a metric is invalid for
# this account. The account metrics split by response shape, not by subject matter:
ACCOUNT_TIME_SERIES_METRICS = ["views"]  # values: [{value, end_time}]
ACCOUNT_TOTAL_VALUE_METRICS = ["likes", "replies", "reposts", "quotes"]  # total_value: {value}
ACCOUNT_LIFETIME_METRICS = ["followers_count"]  # no since/until support

DEMOGRAPHIC_METRIC = "follower_demographics"
# The edge does accept several breakdowns at once, but answers with their cross-product
# (dimension_keys ["age","gender"], dimension_values ["25-34","F"]). One request per breakdown is
# what produces the one-dimension-per-row grain threads_follower_demographics stores.
DEMOGRAPHIC_BREAKDOWNS = ["age", "gender", "country", "city"]

# `clicks` is account-level in the docs but 400s there ("Unsupported request - method type: get")
# in every shape tried — with and without since/until, with and without period. It works per-media,
# so link clicks are collected here instead and land in threads_media_insights.
MEDIA_METRICS = ["views", "likes", "replies", "reposts", "quotes", "shares", "clicks"]


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------


def build_client() -> tuple["ThreadsClient", str]:
    """Build a ThreadsClient from GCP secrets.

    Returns (client, threads_user_id)
    """
    token = get_graph_token(TOKEN_SECRET)
    threads_user_id = get_gcp_secret(USER_ID_SECRET, project_id=PROJECT_ID).strip()
    return ThreadsClient(access_token=token), threads_user_id


# ------------------------------------------------------------------
# API client
# ------------------------------------------------------------------


class ThreadsClient(GraphClient):
    """Graph API client for a Threads account."""

    BASE_URL = BASE_URL  # module constant above; consumed by GraphClient._get

    # ------------------------------------------------------------------
    # User node
    # ------------------------------------------------------------------

    def get_profile(self, threads_user_id: str) -> ThreadsProfile:
        # No try/except: a failure here is an expired or unscoped token, which must stay loud.
        data = self._get(threads_user_id, params={"fields": PROFILE_FIELDS})
        return ThreadsProfile.model_validate(data)

    # ------------------------------------------------------------------
    # User/threads edge
    # ------------------------------------------------------------------

    @staticmethod
    def _build_media(raw_media: list[dict]) -> list[ThreadsMedia]:
        return [ThreadsMedia.model_validate(raw) for raw in raw_media]

    def get_media(self, threads_user_id: str, limit: int = 100, max_results: int | None = None) -> list[ThreadsMedia]:
        try:
            raw_media = self._paginate(
                f"{threads_user_id}/threads",
                params={"fields": MEDIA_FIELDS, "limit": limit},
                max_results=max_results,
                raise_on_failure=True,
            )
        except PaginationError as e:
            e.partial_result = self._build_media(e.items)
            raise
        return self._build_media(raw_media)

    # ------------------------------------------------------------------
    # User/replies edge
    # ------------------------------------------------------------------

    @staticmethod
    def _build_replies(raw_replies: list[dict]) -> list[ThreadsReply]:
        return [ThreadsReply.model_validate(raw) for raw in raw_replies]

    def get_replies(
        self, threads_user_id: str, limit: int = 100, max_results: int | None = None
    ) -> list[ThreadsReply]:
        try:
            raw_replies = self._paginate(
                f"{threads_user_id}/replies",
                params={"fields": REPLY_FIELDS, "limit": limit},
                max_results=max_results,
                raise_on_failure=True,
            )
        except PaginationError as e:
            e.partial_result = self._build_replies(e.items)
            raise
        return self._build_replies(raw_replies)

    # ------------------------------------------------------------------
    # Media/insights edge
    # ------------------------------------------------------------------

    @staticmethod
    def _build_media_insights(items: list[dict]) -> list[ThreadsMediaInsightMetric]:
        return [
            ThreadsMediaInsightMetric(
                name=item["name"],
                period=item.get("period"),
                value=_extract_scalar_value(item),
            )
            for item in items
        ]

    def get_media_insights(self, media: ThreadsMedia, metrics: list[str] | None = None) -> ThreadsMedia:
        """Attach insights to one media item.

        Raises InsightMetricError carrying a partial_result rather than swallowing the failure —
        threads/main.py runs this through src.meta.insights.run_insight_fetch, which needs the
        exception to know which metric failed on which post.
        """
        if metrics is None:
            metrics = MEDIA_METRICS
        # The media insights edge takes no period parameter; period comes back on each item.
        try:
            items = self._fetch_insight_items(f"{media.id}/insights", metrics, {}, raise_on_failure=True)
        except InsightMetricError as e:
            e.partial_result = media.model_copy(update={"insights": self._build_media_insights(e.items)})
            raise
        return media.model_copy(update={"insights": self._build_media_insights(items)})

    # ------------------------------------------------------------------
    # User/threads_insights edge
    # ------------------------------------------------------------------

    def get_account_insights(
        self,
        threads_user_id: str,
        since: date,
        until: date,
        metrics: list[str] | None = None,
    ) -> list[ThreadsAccountInsightMetric]:
        """Time-windowed account metrics, both the values[] and total_value shapes."""
        if metrics is None:
            metrics = ACCOUNT_TIME_SERIES_METRICS + ACCOUNT_TOTAL_VALUE_METRICS
        if not metrics:
            return []
        try:
            items = self._fetch_insight_items(
                f"{threads_user_id}/threads_insights",
                metrics,
                _window_params(since, until),
                raise_on_failure=True,
            )
        except InsightMetricError as e:
            e.partial_result = _clip_to_window([_parse_insight_metric(item) for item in e.items], since, until)
            raise
        return _clip_to_window([_parse_insight_metric(item) for item in items], since, until)

    def get_account_lifetime_insights(
        self, threads_user_id: str, metrics: list[str] | None = None
    ) -> list[ThreadsAccountInsightMetric]:
        """Lifetime account metrics — no since/until, so this is "what is true right now"."""
        if metrics is None:
            metrics = ACCOUNT_LIFETIME_METRICS
        if not metrics:
            return []
        try:
            items = self._fetch_insight_items(
                f"{threads_user_id}/threads_insights", metrics, {}, raise_on_failure=True
            )
        except InsightMetricError as e:
            e.partial_result = [_parse_insight_metric(item) for item in e.items]
            raise
        return [_parse_insight_metric(item) for item in items]

    def get_follower_demographics(
        self, threads_user_id: str, breakdowns: list[str] | None = None
    ) -> list[ThreadsDemographicRow]:
        """One request per breakdown — see DEMOGRAPHIC_BREAKDOWNS for why, it is not an API limit.

        A breakdown that fails is warned about and skipped rather than taking the other three down
        with it, matching how an unavailable metric degrades rather than aborts elsewhere.
        """
        if breakdowns is None:
            breakdowns = DEMOGRAPHIC_BREAKDOWNS
        rows: list[ThreadsDemographicRow] = []
        for breakdown in breakdowns:
            try:
                data = self._get(
                    f"{threads_user_id}/threads_insights",
                    params={"metric": DEMOGRAPHIC_METRIC, "breakdown": breakdown},
                )
            except requests.RequestException as e:
                logger.warning("Could not fetch %s breakdown for %s: %s", breakdown, DEMOGRAPHIC_METRIC, e)
                continue
            parsed = [row for item in data.get("data", []) for row in _parse_demographics(item, breakdown)]
            if not parsed:
                # Below 100 followers the edge answers with an empty object instead of a 400.
                logger.warning("No %s demographic data returned — the 100-follower floor may not be met", breakdown)
            rows.extend(parsed)
        return rows


# ------------------------------------------------------------------
# Parsing helpers
# ------------------------------------------------------------------


def _to_unix(d: date) -> int:
    """Convert a date to the midnight-UTC Unix timestamp this edge expects for since/until."""
    earliest = date.today() - timedelta(days=INSIGHTS_RETENTION_DAYS - 1)
    if d < earliest:
        raise ValueError(
            f"Threads insights retain {INSIGHTS_RETENTION_DAYS} days, so nothing before "
            f"{earliest.isoformat()}; {d.isoformat()} is older and the API rejects it with a fatal 400"
        )
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def _window_params(since: date, until: date) -> dict:
    """since/until as Unix timestamps, with until pushed a day out for an inclusive upper bound.

    The +1 lives here rather than in main.py so callers pass the dates they mean, matching
    instagram/api.py. _to_unix guards `since`; `until` is derived from it and needs no check.
    """
    return {"since": _to_unix(since), "until": _to_unix(until + timedelta(days=1))}


def _clip_to_window(
    metrics: list[ThreadsAccountInsightMetric], since: date, until: date
) -> list[ThreadsAccountInsightMetric]:
    """Drop day buckets that fall outside the window the caller asked for.

    threads_insights answers a one-day window with two day buckets — the requested day and the one
    before it — where the Instagram edge returns one. Keeping the extra would put the same
    (metric, end_time) in two target_date partitions, and per-partition WRITE_TRUNCATE cannot
    dedupe across them: whichever ran last would simply have both.

    Buckets with no end_time are always kept; the total_value metrics describe the whole window
    rather than a day within it. Note end_time is 07:00 UTC — Meta anchors these buckets to
    midnight America/Los_Angeles — so a bucket labelled with the target date covers the Pacific
    day before it. That is the same labelling instagram_account_insights already stores.
    """
    clipped: list[ThreadsAccountInsightMetric] = []
    for metric in metrics:
        values = [v for v in metric.values if v.end_time is None or since <= v.end_time.date() <= until]
        clipped.append(metric.model_copy(update={"values": values}))
    return clipped


def _extract_scalar_value(item: dict) -> int | None:
    """Pull a scalar out of a media insight item, which may use either response shape."""
    values = item.get("values") or []
    if values:
        raw = values[0].get("value")
        return raw if isinstance(raw, int) else None

    total_value = item.get("total_value")
    if isinstance(total_value, dict):
        raw = total_value.get("value")
        return raw if isinstance(raw, int) else None

    raw = item.get("value")
    return raw if isinstance(raw, int) else None


def _parse_insight_metric(item: dict) -> ThreadsAccountInsightMetric:
    """Parse one account insight item, normalising total_value into a one-entry values list.

    Flattening the two shapes here is what lets threads/schemas.py stay a plain loop over
    metric.values.
    """
    values: list[ThreadsInsightValue] = []

    raw_values = item.get("values") or []
    if raw_values:
        for v in raw_values:
            raw = v.get("value")
            if isinstance(raw, (int, float)):
                values.append(ThreadsInsightValue(end_time=v.get("end_time"), value=raw))
    elif "total_value" in item:
        total_value = item.get("total_value") or {}
        raw = total_value.get("value")
        if isinstance(raw, (int, float)):
            values.append(ThreadsInsightValue(end_time=total_value.get("end_time"), value=raw))

    return ThreadsAccountInsightMetric(name=item["name"], period=item.get("period"), values=values)


def _parse_demographics(item: dict, breakdown: str) -> list[ThreadsDemographicRow]:
    """Flatten one follower_demographics response into (breakdown, dimension_value, value) rows.

    Two shapes are handled. The one this account actually returns nests as
    total_value.breakdowns[].results[].dimension_values[0]; the published docs show a flatter
    breakdown: [{country: "US", value: N}]. Neither present — an empty {} below the 100-follower
    floor — returns [] rather than raising.
    """
    rows: list[ThreadsDemographicRow] = []

    total_value = item.get("total_value") or {}
    for group in total_value.get("breakdowns") or []:
        for result in group.get("results") or []:
            dimension_values = result.get("dimension_values") or []
            if not dimension_values:
                continue
            rows.append(
                ThreadsDemographicRow(
                    breakdown=breakdown,
                    dimension_value=str(dimension_values[0]),
                    value=result.get("value"),
                )
            )
    if rows:
        return rows

    for entry in item.get("breakdown") or []:
        # The documented shape names the dimension by its own key, e.g. {"country": "US", "value": N}.
        keys = [k for k in entry if k != "value"]
        if not keys:
            continue
        rows.append(
            ThreadsDemographicRow(
                breakdown=breakdown,
                dimension_value=str(entry[keys[0]]),
                value=entry.get("value"),
            )
        )
    return rows
