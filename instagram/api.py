import logging
import time
from datetime import date, timedelta

import requests

from instagram.models import IGAccount, IGAccountInsightMetric, IGInsightValue, IGMedia, IGMediaInsightMetric
from src.authentication import get_gcp_secret

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
    token = get_gcp_secret(TOKEN_SECRET, project_id=PROJECT_ID).strip()
    ig_user_id = get_gcp_secret(IG_USER_ID_SECRET, project_id=PROJECT_ID).strip()
    return InstagramClient(access_token=token), ig_user_id


def daterange_chunks(start_date: date, end_date: date, chunk_days: int = 30) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cur = start_date
    while cur <= end_date:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end_date)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


class InstagramClient:
    def __init__(self, access_token: str) -> None:
        self._session = requests.Session()
        self._session.params = {"access_token": access_token}  # type: ignore[assignment]

    def _get(self, path: str, params: dict | None = None, retries: int = 3) -> dict:
        url = path if path.startswith("http") else f"{BASE_URL}/{path.lstrip('/')}"
        for attempt in range(retries):
            response = self._session.get(url, params=params or {}, timeout=30)
            if response.status_code == 429:
                logger.warning("Rate limited, waiting 60s")
                time.sleep(60)
                continue
            if response.status_code >= 500:
                wait = 2**attempt
                logger.warning("HTTP %d, retrying in %ds", response.status_code, wait)
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        response.raise_for_status()

    def _paginate(self, path: str, params: dict | None = None, max_results: int | None = None) -> list[dict]:
        results: list[dict] = []
        data = self._get(path, params)
        results.extend(data.get("data", []))
        while max_results is None or len(results) < max_results:
            next_url = data.get("paging", {}).get("next")
            if not next_url:
                break
            data = self._get(next_url)
            results.extend(data.get("data", []))
        return results[:max_results] if max_results is not None else results

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
                "fields": "id,caption,media_type,media_product_type,permalink,thumbnail_url,media_url,timestamp,like_count,comments_count",
                "limit": limit,
            },
            max_results=max_results,
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
        except requests.HTTPError as e:
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
