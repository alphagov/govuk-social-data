import logging
from datetime import date, timedelta

import requests

from instagram.models import IGAccount, IGAccountInsightMetric, IGInsightValue, IGMedia, IGMediaInsightMetric
from src.authentication import get_gcp_secret
from src.meta.auth import get_graph_token
from src.meta.client import GraphClient

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v25.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"

PROJECT_ID = "gds-social-data"
TOKEN_SECRET = "instagram_bearer_token"
IG_USER_ID_SECRET = "instagram_user_id"

# Update these lists if Step 1 HTTP tests reveal any metrics are invalid.
ACCOUNT_DAY_METRICS = [
    "reach",
    "follower_count",  # only supports the last 30 days; do not query older dates
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


def build_client() -> tuple["InstagramClient", str]:
    """Build an InstagramClient from GCP secrets.

    Returns (client, ig_user_id). The ig_user_id is returned alongside the
    client because most API calls require it as an explicit path parameter.

    Instagram long-lived tokens are stored as plain strings in Secret Manager,
    unlike the Facebook token which is JSON-wrapped.
    """
    token = get_graph_token(TOKEN_SECRET)
    ig_user_id = get_gcp_secret(IG_USER_ID_SECRET, project_id=PROJECT_ID).strip()
    return InstagramClient(access_token=token), ig_user_id


class InstagramClient(GraphClient):
    """Graph API client for an Instagram Business account.

    Transport — _get with its retry/backoff, _paginate, _next_page and _fetch_insight_items —
    is inherited from GraphClient and deliberately not overridden here. This class supplies the
    base URL, the metric constants and the model builders.
    """

    BASE_URL = BASE_URL  # module constant above; consumed by GraphClient._get

    # ------------------------------------------------------------------
    # Account node
    # ------------------------------------------------------------------

    def get_account(self, ig_user_id: str) -> IGAccount:
        data = self._get(
            ig_user_id,
            params={"fields": "id,username,name,biography,followers_count,follows_count,media_count"},
        )
        return IGAccount.model_validate(data)

    # ------------------------------------------------------------------
    # Account/insights edge — day-period metrics
    # ------------------------------------------------------------------

    def get_account_insights(
        self,
        ig_user_id: str,
        since: date,
        until: date,
        metrics: list[str] | None = None,
    ) -> list[IGAccountInsightMetric]:
        if metrics is None:
            metrics = ACCOUNT_DAY_METRICS
        if not metrics:
            return []

        # Meta documents since/until as lower/upper bounds; add one day for inclusive upper bound.
        request_until = until + timedelta(days=1)
        data = self._get(
            f"{ig_user_id}/insights",
            params={
                "metric": ",".join(metrics),
                "period": "day",
                "since": since.isoformat(),
                "until": request_until.isoformat(),
            },
        )
        return [_parse_insight_metric(item) for item in data.get("data", [])]

    # ------------------------------------------------------------------
    # Account/insights edge — total_value metrics (e.g. views)
    # ------------------------------------------------------------------

    def get_account_total_value_insights(
        self,
        ig_user_id: str,
        since: date,
        until: date,
        metrics: list[str] | None = None,
    ) -> list[IGAccountInsightMetric]:
        if metrics is None:
            metrics = ACCOUNT_TOTAL_VALUE_METRICS
        if not metrics:
            return []

        request_until = until + timedelta(days=1)
        data = self._get(
            f"{ig_user_id}/insights",
            params={
                "metric": ",".join(metrics),
                "metric_type": "total_value",
                "period": "day",
                "since": since.isoformat(),
                "until": request_until.isoformat(),
            },
        )
        return [_parse_insight_metric(item) for item in data.get("data", [])]

    # ------------------------------------------------------------------
    # Account/media edge
    # ------------------------------------------------------------------

    def get_media(
        self,
        ig_user_id: str,
        limit: int = 100,
        max_results: int | None = None,
    ) -> list[IGMedia]:
        raw = self._paginate(
            f"{ig_user_id}/media",
            params={
                "fields": (
                    "id,caption,media_type,media_product_type,permalink,"
                    "thumbnail_url,media_url,timestamp,like_count,comments_count"
                ),
                "limit": limit,
            },
            max_results=max_results,
            # Preserves today's semantics rather than changing them: this module has no
            # try/except, so a pagination failure has always killed the run. The inherited
            # default (False) would instead return a partial list that upload_partitioned
            # WRITE_TRUNCATEs over a good partition — a 4,000-item account failing at page 14
            # would land 1,300 rows and exit 0. Batch 6 turns this into a partial_result.
            raise_on_failure=True,
        )
        return [IGMedia.model_validate(item) for item in raw]

    # ------------------------------------------------------------------
    # Media/insights edge
    # ------------------------------------------------------------------

    def get_media_insights(
        self,
        media_id: str,
        metrics: list[str] | None = None,
    ) -> list[IGMediaInsightMetric]:
        if metrics is None:
            metrics = MEDIA_LIFETIME_METRICS
        try:
            data = self._get(
                f"{media_id}/insights",
                params={"metric": ",".join(metrics), "period": "lifetime"},
            )
        except requests.RequestException as e:
            logger.warning("Could not fetch insights for media %s: %s", media_id, e)
            return []
        return [
            IGMediaInsightMetric(
                name=item["name"],
                period=item.get("period", "lifetime"),
                value=_extract_scalar_value(item),
            )
            for item in data.get("data", [])
        ]


# ------------------------------------------------------------------
# Parsing helpers
# ------------------------------------------------------------------


def _extract_scalar_value(item: dict) -> int | None:
    """Extract a scalar integer value from an insight item.

    The Graph API uses different shapes for different metric types:
    - Standard: {"values": [{"value": 123, "end_time": "..."}]}
    - total_value: {"total_value": {"value": 123}}
    - Direct: {"value": 123}
    """
    values = item.get("values", [])
    if values:
        raw = values[0].get("value")
        return raw if isinstance(raw, int) else None

    total_value = item.get("total_value")
    if isinstance(total_value, dict):
        raw = total_value.get("value")
        return raw if isinstance(raw, int) else None

    raw = item.get("value")
    return raw if isinstance(raw, int) else None


def _parse_insight_metric(item: dict) -> IGAccountInsightMetric:
    """Parse a single insight object into an IGAccountInsightMetric.

    Handles both standard (values list) and total_value response shapes.
    """
    values: list[IGInsightValue] = []

    raw_values = item.get("values", [])
    if raw_values:
        for v in raw_values:
            raw = v.get("value")
            numeric = raw if isinstance(raw, (int, float)) else None
            if numeric is not None:
                values.append(IGInsightValue(end_time=v.get("end_time"), value=numeric))
    elif "total_value" in item:
        total_value_obj = item.get("total_value") or {}
        raw = total_value_obj.get("value")
        if isinstance(raw, (int, float)):
            values.append(IGInsightValue(end_time=total_value_obj.get("end_time"), value=raw))

    return IGAccountInsightMetric(
        name=item["name"],
        period=item.get("period", "day"),
        values=values,
    )
