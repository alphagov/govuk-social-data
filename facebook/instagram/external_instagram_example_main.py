import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests
from google.cloud import bigquery
from google.cloud import secretmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ig_insights_job")

# ----------------------------
# Config / environment variables
# These control the GCP project, dataset, secrets, API version and run mode.
# Metric lists are kept configurable so we can adjust them without changing core logic.
# ----------------------------
PROJECT_ID = os.getenv("PROJECT_ID", "gds-social-data")
BQ_DATASET = os.getenv("BQ_DATASET", "govuk_instagram_channel")
BQ_LOCATION = os.getenv("BQ_LOCATION", "EU")
GRAPH_VERSION = os.getenv("GRAPH_VERSION", "v25.0")

TOKEN_SECRET = os.getenv("TOKEN_JSON_SECRET", "instagram_bearer_token")
TOKEN_SECRET_VERSION = os.getenv("TOKEN_SECRET_VERSION", "latest")
IG_USER_ID_SECRET = os.getenv("IG_USER_ID_SECRET", "instagram_user_id")

MODE = os.getenv("MODE", "daily")  # daily | backfill | probe
BACKFILL_START = os.getenv("BACKFILL_START")
BACKFILL_END = os.getenv("BACKFILL_END")
CHUNK_DAYS = int(os.getenv("CHUNK_DAYS", "30"))
MEDIA_PROBE_LIMIT = int(os.getenv("MEDIA_PROBE_LIMIT", "5"))
MEDIA_FETCH_LIMIT = int(os.getenv("MEDIA_FETCH_LIMIT", "100"))

DEFAULT_ACCOUNT_METRICS = json.loads(
    os.getenv(
        "ACCOUNT_METRICS_JSON",
        json.dumps([
            "reach",
            "follower_count",
        ]),
    )
)

DEFAULT_ACCOUNT_TOTAL_VALUE_METRICS = json.loads(
    os.getenv(
        "ACCOUNT_TOTAL_VALUE_METRICS_JSON",
        json.dumps([
            "views",
        ]),
    )
)

# Instagram media insights are being stored as lifetime snapshots only.
# Keep the metric list configurable so unsupported metrics can be removed easily.
DEFAULT_MEDIA_LIFETIME_METRICS = json.loads(
    os.getenv(
        "MEDIA_LIFETIME_METRICS_JSON",
        json.dumps([
            "reach",
            "views",
            "likes",
            "comments",
            "saved",
            "shares",
            "total_interactions",
        ]),
    )
)

BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"

DIM_ACCOUNT_TABLE = f"{PROJECT_ID}.{BQ_DATASET}.dim_instagram_account"
RAW_ACCOUNT_INSIGHTS_TABLE = f"{PROJECT_ID}.{BQ_DATASET}.raw_instagram_account_insights"
ACCOUNT_INSIGHTS_DAILY_TABLE = f"{PROJECT_ID}.{BQ_DATASET}.instagram_account_insights_daily"
MEDIA_TABLE = f"{PROJECT_ID}.{BQ_DATASET}.instagram_media"
RAW_MEDIA_INSIGHTS_TABLE = f"{PROJECT_ID}.{BQ_DATASET}.raw_instagram_media_insights"
MEDIA_INSIGHTS_LIFETIME_TABLE = f"{PROJECT_ID}.{BQ_DATASET}.instagram_media_insights_lifetime"
RUN_LOG_TABLE = f"{PROJECT_ID}.{BQ_DATASET}.ig_run_log"


# ----------------------------
# Secret Manager helpers
# Read the Instagram token and IG user id from Secret Manager
# so we do not hardcode credentials in the repo.
# ----------------------------
def get_secret_value(secret_name: str, version: str = "latest") -> str:
    sm_client = secretmanager.SecretManagerServiceClient()
    resource_name = f"projects/{PROJECT_ID}/secrets/{secret_name}/versions/{version}"
    return sm_client.access_secret_version(name=resource_name).payload.data.decode("utf-8").strip()


def get_access_token() -> str:
    return get_secret_value(TOKEN_SECRET, TOKEN_SECRET_VERSION)


def get_ig_user_id() -> str:
    return get_secret_value(IG_USER_ID_SECRET, "latest")


# ----------------------------
# API helper
# Wrapper for Instagram Graph API GET requests.
# Logs the response body when a request fails to make debugging easier.
# ----------------------------
def api_get(path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = path_or_url if path_or_url.startswith("http") else f"{BASE_URL}/{path_or_url.lstrip('/')}"
    r = requests.get(url, params=params, timeout=60)
    if r.status_code != 200:
        logger.error("Instagram API error status=%s url=%s body=%s", r.status_code, r.url, r.text[:4000])
        r.raise_for_status()
    return r.json()


# ----------------------------
# BigQuery table setup
# Create the required tables if they do not already exist.
# Media insights are stored as raw plus lifetime snapshot tables.
# ----------------------------
def ensure_tables(bq_client: bigquery.Client) -> None:
    schemas = {
        DIM_ACCOUNT_TABLE: [
            bigquery.SchemaField("ig_user_id", "STRING"),
            bigquery.SchemaField("username", "STRING"),
            bigquery.SchemaField("name", "STRING"),
            bigquery.SchemaField("biography", "STRING"),
            bigquery.SchemaField("followers_count", "INT64"),
            bigquery.SchemaField("follows_count", "INT64"),
            bigquery.SchemaField("media_count", "INT64"),
            bigquery.SchemaField("updated_ts", "TIMESTAMP"),
        ],
        RAW_ACCOUNT_INSIGHTS_TABLE: [
            bigquery.SchemaField("run_ts", "TIMESTAMP"),
            bigquery.SchemaField("ig_user_id", "STRING"),
            bigquery.SchemaField("metric", "STRING"),
            bigquery.SchemaField("period", "STRING"),
            bigquery.SchemaField("title", "STRING"),
            bigquery.SchemaField("description", "STRING"),
            bigquery.SchemaField("end_time_ts", "TIMESTAMP"),
            bigquery.SchemaField("metric_date", "DATE"),
            bigquery.SchemaField("value", "INT64"),
            bigquery.SchemaField("value_json", "STRING"),
        ],
        ACCOUNT_INSIGHTS_DAILY_TABLE: [
            bigquery.SchemaField("run_ts", "TIMESTAMP"),
            bigquery.SchemaField("ig_user_id", "STRING"),
            bigquery.SchemaField("metric", "STRING"),
            bigquery.SchemaField("period", "STRING"),
            bigquery.SchemaField("title", "STRING"),
            bigquery.SchemaField("metric_description", "STRING"),
            bigquery.SchemaField("end_time_ts", "TIMESTAMP"),
            bigquery.SchemaField("metric_date", "DATE"),
            bigquery.SchemaField("value", "INT64"),
            bigquery.SchemaField("value_json", "STRING"),
        ],
        MEDIA_TABLE: [
            bigquery.SchemaField("run_ts", "TIMESTAMP"),
            bigquery.SchemaField("ig_user_id", "STRING"),
            bigquery.SchemaField("media_id", "STRING"),
            bigquery.SchemaField("caption", "STRING"),
            bigquery.SchemaField("media_type", "STRING"),
            bigquery.SchemaField("media_product_type", "STRING"),
            bigquery.SchemaField("permalink", "STRING"),
            bigquery.SchemaField("thumbnail_url", "STRING"),
            bigquery.SchemaField("media_url", "STRING"),
            bigquery.SchemaField("media_timestamp_ts", "TIMESTAMP"),
            bigquery.SchemaField("like_count", "INT64"),
            bigquery.SchemaField("comments_count", "INT64"),
            bigquery.SchemaField("raw_json", "STRING"),
        ],
        RAW_MEDIA_INSIGHTS_TABLE: [
            bigquery.SchemaField("run_ts", "TIMESTAMP"),
            bigquery.SchemaField("ig_user_id", "STRING"),
            bigquery.SchemaField("media_id", "STRING"),
            bigquery.SchemaField("metric", "STRING"),
            bigquery.SchemaField("period", "STRING"),
            bigquery.SchemaField("title", "STRING"),
            bigquery.SchemaField("description", "STRING"),
            bigquery.SchemaField("end_time_ts", "TIMESTAMP"),
            bigquery.SchemaField("metric_date", "DATE"),
            bigquery.SchemaField("value", "INT64"),
            bigquery.SchemaField("value_json", "STRING"),
        ],
        MEDIA_INSIGHTS_LIFETIME_TABLE: [
            bigquery.SchemaField("run_ts", "TIMESTAMP"),
            bigquery.SchemaField("ig_user_id", "STRING"),
            bigquery.SchemaField("media_id", "STRING"),
            bigquery.SchemaField("metric", "STRING"),
            bigquery.SchemaField("period", "STRING"),
            bigquery.SchemaField("title", "STRING"),
            bigquery.SchemaField("metric_description", "STRING"),
            bigquery.SchemaField("snapshot_date", "DATE"),
            bigquery.SchemaField("value", "INT64"),
            bigquery.SchemaField("value_json", "STRING"),
        ],
        RUN_LOG_TABLE: [
            bigquery.SchemaField("run_ts", "TIMESTAMP"),
            bigquery.SchemaField("status", "STRING"),
            bigquery.SchemaField("window_start", "DATE"),
            bigquery.SchemaField("window_end", "DATE"),
            bigquery.SchemaField("rows_raw_loaded", "INT64"),
            bigquery.SchemaField("rows_curated_merged", "INT64"),
            bigquery.SchemaField("rows_media_loaded", "INT64"),
            bigquery.SchemaField("rows_media_insights_lifetime_loaded", "INT64"),
            bigquery.SchemaField("landed_yesterday", "BOOL"),
            bigquery.SchemaField("error", "STRING"),
        ],
    }

    for table_id, schema in schemas.items():
        try:
            bq_client.get_table(table_id)
        except Exception:
            table = bigquery.Table(table_id, schema=schema)
            bq_client.create_table(table)
            logger.info("Created table %s", table_id)


# ----------------------------
# BigQuery load helpers
# Reusable helpers for loading dataframes and
# upserting the Instagram account dimension table.
# ----------------------------
def load_dataframe(
    bq_client: bigquery.Client,
    df: pd.DataFrame,
    table_id: str,
    write_disposition: str = "WRITE_APPEND",
) -> int:
    if df.empty:
        return 0
    job_config = bigquery.LoadJobConfig(write_disposition=write_disposition)
    bq_client.load_table_from_dataframe(df, table_id, job_config=job_config).result()
    return len(df)


def upsert_dim_account(bq_client: bigquery.Client, ig_user_id: str, account: dict[str, Any]) -> int:
    row = {
        "ig_user_id": ig_user_id,
        "username": account.get("username"),
        "name": account.get("name"),
        "biography": account.get("biography"),
        "followers_count": account.get("followers_count"),
        "follows_count": account.get("follows_count"),
        "media_count": account.get("media_count"),
        "updated_ts": datetime.now(timezone.utc),
    }
    staging = pd.DataFrame([row])
    tmp_table = f"{PROJECT_ID}.{BQ_DATASET}._tmp_dim_instagram_account"
    load_dataframe(bq_client, staging, tmp_table, write_disposition="WRITE_TRUNCATE")

    query = f"""
    MERGE `{DIM_ACCOUNT_TABLE}` T
    USING `{tmp_table}` S
    ON T.ig_user_id = S.ig_user_id
    WHEN MATCHED THEN UPDATE SET
      username = S.username,
      name = S.name,
      biography = S.biography,
      followers_count = S.followers_count,
      follows_count = S.follows_count,
      media_count = S.media_count,
      updated_ts = S.updated_ts
    WHEN NOT MATCHED THEN INSERT ROW
    """
    bq_client.query(query, location=BQ_LOCATION).result()
    return 1


# ----------------------------
# Instagram account fetch
# Pull core account metadata such as username, biography,
# follower counts and media count for the linked GOV.UK account.
# ----------------------------
def fetch_account(ig_user_id: str, token: str) -> dict[str, Any]:
    return api_get(
        ig_user_id,
        {
            "access_token": token,
            "fields": "id,username,name,biography,followers_count,follows_count,media_count",
        },
    )


# ----------------------------
# Account insights
# Fetch and flatten account-level metrics into a row-based format
# so they can be loaded into both raw and curated tables.
# Views is requested separately because it uses total_value.
# ----------------------------
def fetch_account_insights(
    ig_user_id: str,
    token: str,
    since: date,
    until: date,
    metrics: list[str],
) -> list[dict[str, Any]]:
    if not metrics:
        return []

    # Use next day as upper bound for a single-day window.
    # Meta documents since/until as lower/upper bounds.
    request_until = until + timedelta(days=1)

    payload = api_get(
        f"{ig_user_id}/insights",
        {
            "access_token": token,
            "metric": ",".join(metrics),
            "period": "day",
            "since": since.isoformat(),
            "until": request_until.isoformat(),
        },
    )
    return payload.get("data", [])


def fetch_account_total_value_insights(
    ig_user_id: str,
    token: str,
    since: date,
    until: date,
    metrics: list[str],
) -> list[dict[str, Any]]:
    if not metrics:
        return []

    # Use next day as upper bound for a single-day window.
    request_until = until + timedelta(days=1)

    payload = api_get(
        f"{ig_user_id}/insights",
        {
            "access_token": token,
            "metric": ",".join(metrics),
            "metric_type": "total_value",
            "period": "day",
            "since": since.isoformat(),
            "until": request_until.isoformat(),
        },
    )
    return payload.get("data", [])


def flatten_account_insights(ig_user_id: str, insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    run_ts = datetime.now(timezone.utc)

    for metric_obj in insights:
        metric_name = metric_obj.get("name")
        period = metric_obj.get("period")
        title = metric_obj.get("title")
        description = metric_obj.get("description")

        # Standard response shape for most account metrics
        values = metric_obj.get("values", [])

        # Handle total_value response shape used by views
        if not values and "total_value" in metric_obj:
            total_value_obj = metric_obj.get("total_value") or {}
            values = [
                {
                    "value": total_value_obj.get("value"),
                    "end_time": total_value_obj.get("end_time"),
                }
            ]

        # Fallback for any direct value shape
        if not values and "value" in metric_obj:
            values = [
                {
                    "value": metric_obj.get("value"),
                    "end_time": metric_obj.get("end_time"),
                }
            ]

        for val in values:
            value = val.get("value")
            end_time = val.get("end_time")

            numeric_value = value if isinstance(value, int) else None
            value_json = None if isinstance(value, int) else json.dumps(value)

            end_time_ts = pd.to_datetime(end_time, utc=True) if end_time else None
            metric_date = end_time_ts.date() if end_time_ts is not None else None

            out.append(
                {
                    "run_ts": run_ts,
                    "ig_user_id": ig_user_id,
                    "metric": metric_name,
                    "period": period,
                    "title": title,
                    "description": description,
                    "metric_description": description,
                    "end_time_ts": end_time_ts,
                    "metric_date": metric_date,
                    "value": numeric_value,
                    "value_json": value_json,
                }
            )

    return out


# ----------------------------
# Media fetch
# Pull recent Instagram media objects and basic metadata
# such as caption, media type, permalink and engagement counts.
# ----------------------------
def fetch_media(ig_user_id: str, token: str, limit: int = 100) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    next_url: str | None = None

    while True:
        if next_url:
            payload = api_get(next_url)
        else:
            payload = api_get(
                f"{ig_user_id}/media",
                {
                    "access_token": token,
                    "fields": "id,caption,media_type,media_product_type,permalink,thumbnail_url,media_url,timestamp,like_count,comments_count",
                    "limit": limit,
                },
            )

        out.extend(payload.get("data", []))
        next_url = payload.get("paging", {}).get("next")
        if not next_url:
            break

    return out


def flatten_media(ig_user_id: str, media_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    run_ts = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []

    for m in media_rows:
        out.append(
            {
                "run_ts": run_ts,
                "ig_user_id": ig_user_id,
                "media_id": m.get("id"),
                "caption": m.get("caption"),
                "media_type": m.get("media_type"),
                "media_product_type": m.get("media_product_type"),
                "permalink": m.get("permalink"),
                "thumbnail_url": m.get("thumbnail_url"),
                "media_url": m.get("media_url"),
                "media_timestamp_ts": pd.to_datetime(m.get("timestamp"), utc=True) if m.get("timestamp") else None,
                "like_count": m.get("like_count"),
                "comments_count": m.get("comments_count"),
                "raw_json": json.dumps(m),
            }
        )
    return out


# ----------------------------
# Media insights
# Instagram media insights are stored as lifetime snapshots.
# We keep the raw response as well so any future metric changes are traceable.
# ----------------------------
def fetch_media_insights(media_id: str, token: str, metrics: list[str], period: str) -> list[dict[str, Any]]:
    payload = api_get(
        f"{media_id}/insights",
        {
            "access_token": token,
            "metric": ",".join(metrics),
            "period": period,
        },
    )
    return payload.get("data", [])


def flatten_media_insights(
    ig_user_id: str,
    media_id: str,
    insights: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    run_ts = datetime.now(timezone.utc)
    snapshot_date = run_ts.date()
    out: list[dict[str, Any]] = []

    for metric_obj in insights:
        period = metric_obj.get("period")
        title = metric_obj.get("title")
        description = metric_obj.get("description")
        metric_name = metric_obj.get("name")

        values = metric_obj.get("values", [])
        if not values and "value" in metric_obj:
            values = [{"value": metric_obj.get("value"), "end_time": None}]

        for val in values:
            value = val.get("value")
            end_time = val.get("end_time")
            numeric_value = value if isinstance(value, int) else None
            value_json = None if isinstance(value, int) else json.dumps(value)
            end_time_ts = pd.to_datetime(end_time, utc=True) if end_time else None
            metric_date = end_time_ts.date() if end_time_ts is not None else None

            out.append(
                {
                    "run_ts": run_ts,
                    "ig_user_id": ig_user_id,
                    "media_id": media_id,
                    "metric": metric_name,
                    "period": period,
                    "title": title,
                    "description": description,
                    "metric_description": description,
                    "end_time_ts": end_time_ts,
                    "metric_date": metric_date,
                    "snapshot_date": snapshot_date,
                    "value": numeric_value,
                    "value_json": value_json,
                }
            )

    return out


# ----------------------------
# Probe mode
# Test a small sample of recent media first to confirm which metrics
# are actually returned before running the full ingestion.
# Media insights are checked as lifetime because that is what is being stored.
# ----------------------------
def probe_media_metrics(token: str, ig_user_id: str, media_rows: list[dict[str, Any]]) -> None:
    sample = media_rows[:MEDIA_PROBE_LIMIT]
    logger.info("Probe mode using %s media items", len(sample))

    for m in sample:
        media_id = m.get("id")
        media_type = m.get("media_type")
        logger.info("Probing media_id=%s media_type=%s permalink=%s", media_id, media_type, m.get("permalink"))

        try:
            payload = api_get(
                f"{media_id}/insights",
                {
                    "access_token": token,
                    "metric": ",".join(DEFAULT_MEDIA_LIFETIME_METRICS),
                    "period": "lifetime",
                },
            )
            names = [x.get("name") for x in payload.get("data", [])]
            periods = [x.get("period") for x in payload.get("data", [])]
            logger.info("Probe OK media_id=%s returned_metrics=%s returned_periods=%s", media_id, names, periods)
        except Exception as exc:
            logger.warning("Probe FAIL media_id=%s error=%s", media_id, str(exc)[:1000])


# ----------------------------
# Date window helpers
# Split longer backfill periods into chunks so runs are easier to manage
# and less likely to fail on large ranges.
# ----------------------------
def daterange_chunks(start_date: date, end_date: date, chunk_days: int) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cur = start_date
    while cur <= end_date:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end_date)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


# ----------------------------
# Run log
# Write a simple audit record for each run so we can track
# what loaded successfully and troubleshoot failures more easily.
# ----------------------------
def log_run(
    bq_client: bigquery.Client,
    *,
    status: str,
    window_start: date | None,
    window_end: date | None,
    rows_raw_loaded: int,
    rows_curated_merged: int,
    rows_media_loaded: int,
    rows_media_insights_lifetime_loaded: int,
    landed_yesterday: bool | None,
    error: str | None,
) -> None:
    row = pd.DataFrame(
        [
            {
                "run_ts": datetime.now(timezone.utc),
                "status": status,
                "window_start": window_start,
                "window_end": window_end,
                "rows_raw_loaded": rows_raw_loaded,
                "rows_curated_merged": rows_curated_merged,
                "rows_media_loaded": rows_media_loaded,
                "rows_media_insights_lifetime_loaded": rows_media_insights_lifetime_loaded,
                "landed_yesterday": landed_yesterday,
                "error": error,
            }
        ]
    )
    load_dataframe(bq_client, row, RUN_LOG_TABLE)


# ----------------------------
# Main pipeline
# Overall flow:
# 1. ensure tables exist
# 2. read secrets
# 3. fetch account and media data
# 4. run probe / daily / backfill logic
# 5. write run log
# ----------------------------
def main() -> None:
    bq_client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)
    ensure_tables(bq_client)

    token = get_access_token()
    ig_user_id = get_ig_user_id()

    rows_raw_loaded = 0
    rows_curated_loaded = 0
    rows_media_loaded = 0
    rows_media_insights_lifetime_loaded = 0

    try:
        account = fetch_account(ig_user_id, token)
        upsert_dim_account(bq_client, ig_user_id, account)
        logger.info("Fetched IG account username=%s media_count=%s", account.get("username"), account.get("media_count"))

        media_rows = fetch_media(ig_user_id, token, limit=MEDIA_FETCH_LIMIT)
        flat_media = flatten_media(ig_user_id, media_rows)
        if flat_media:
            rows_media_loaded += load_dataframe(bq_client, pd.DataFrame(flat_media), MEDIA_TABLE)

        if MODE == "probe":
            probe_media_metrics(token, ig_user_id, media_rows)
            log_run(
                bq_client,
                status="probe_success",
                window_start=None,
                window_end=None,
                rows_raw_loaded=0,
                rows_curated_merged=0,
                rows_media_loaded=rows_media_loaded,
                rows_media_insights_lifetime_loaded=0,
                landed_yesterday=None,
                error=None,
            )
            return

        if MODE == "backfill":
            if not BACKFILL_START or not BACKFILL_END:
                raise ValueError("BACKFILL_START and BACKFILL_END are required when MODE=backfill")
            start_date = date.fromisoformat(BACKFILL_START)
            end_date = date.fromisoformat(BACKFILL_END)
        else:
            yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
            start_date = yesterday
            end_date = yesterday

        for chunk_start, chunk_end in daterange_chunks(start_date, end_date, CHUNK_DAYS):
            logger.info("Processing account insights window %s -> %s", chunk_start, chunk_end)

            standard_insights = fetch_account_insights(
                ig_user_id=ig_user_id,
                token=token,
                since=chunk_start,
                until=chunk_end,
                metrics=DEFAULT_ACCOUNT_METRICS,
            )

            total_value_insights = fetch_account_total_value_insights(
                ig_user_id=ig_user_id,
                token=token,
                since=chunk_start,
                until=chunk_end,
                metrics=DEFAULT_ACCOUNT_TOTAL_VALUE_METRICS,
            )

            insights = standard_insights + total_value_insights
            logger.info("Account insights returned %s objects", len(insights))
            logger.info("Account insight names: %s", [x.get("name") for x in insights])

            flat = flatten_account_insights(ig_user_id, insights)
            logger.info("Flattened account insight rows: %s", len(flat))

            if flat:
                raw_df = pd.DataFrame(
                    [
                        {
                            "run_ts": r["run_ts"],
                            "ig_user_id": r["ig_user_id"],
                            "metric": r["metric"],
                            "period": r["period"],
                            "title": r["title"],
                            "description": r["description"],
                            "end_time_ts": r["end_time_ts"],
                            "metric_date": r["metric_date"],
                            "value": r["value"],
                            "value_json": r["value_json"],
                        }
                        for r in flat
                    ]
                )
                curated_df = pd.DataFrame(
                    [
                        {
                            "run_ts": r["run_ts"],
                            "ig_user_id": r["ig_user_id"],
                            "metric": r["metric"],
                            "period": r["period"],
                            "title": r["title"],
                            "metric_description": r["metric_description"],
                            "end_time_ts": r["end_time_ts"],
                            "metric_date": r["metric_date"],
                            "value": r["value"],
                            "value_json": r["value_json"],
                        }
                        for r in flat
                    ]
                )
                rows_raw_loaded += load_dataframe(bq_client, raw_df, RAW_ACCOUNT_INSIGHTS_TABLE)
                rows_curated_loaded += load_dataframe(bq_client, curated_df, ACCOUNT_INSIGHTS_DAILY_TABLE)

        # Media insights are stored as lifetime snapshots only.
        lifetime_rows_all: list[dict[str, Any]] = []
        raw_media_rows_all: list[dict[str, Any]] = []

        for m in media_rows:
            media_id = m.get("id")
            if not media_id:
                continue

            try:
                lifetime_insights = fetch_media_insights(media_id, token, DEFAULT_MEDIA_LIFETIME_METRICS, "lifetime")
                flat_lifetime = flatten_media_insights(ig_user_id, media_id, lifetime_insights)

                for r in flat_lifetime:
                    raw_media_rows_all.append(
                        {
                            "run_ts": r["run_ts"],
                            "ig_user_id": r["ig_user_id"],
                            "media_id": r["media_id"],
                            "metric": r["metric"],
                            "period": r["period"],
                            "title": r["title"],
                            "description": r["description"],
                            "end_time_ts": r["end_time_ts"],
                            "metric_date": r["metric_date"],
                            "value": r["value"],
                            "value_json": r["value_json"],
                        }
                    )

                    # Only keep rows the API actually returned as lifetime.
                    if r["period"] == "lifetime":
                        lifetime_rows_all.append(
                            {
                                "run_ts": r["run_ts"],
                                "ig_user_id": r["ig_user_id"],
                                "media_id": r["media_id"],
                                "metric": r["metric"],
                                "period": r["period"],
                                "title": r["title"],
                                "metric_description": r["metric_description"],
                                "snapshot_date": r["snapshot_date"],
                                "value": r["value"],
                                "value_json": r["value_json"],
                            }
                        )
            except Exception as exc:
                logger.warning("Skipping lifetime insights for media_id=%s error=%s", media_id, str(exc)[:1000])

        if raw_media_rows_all:
            rows_raw_loaded += load_dataframe(bq_client, pd.DataFrame(raw_media_rows_all), RAW_MEDIA_INSIGHTS_TABLE)
        if lifetime_rows_all:
            rows_media_insights_lifetime_loaded += load_dataframe(
                bq_client,
                pd.DataFrame(lifetime_rows_all),
                MEDIA_INSIGHTS_LIFETIME_TABLE,
            )

        landed_yesterday = False
        if MODE != "backfill":
            q = f"""
            SELECT COUNT(*) AS c
            FROM `{ACCOUNT_INSIGHTS_DAILY_TABLE}`
            WHERE metric_date = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
            """
            result = list(bq_client.query(q, location=BQ_LOCATION).result())
            landed_yesterday = (result[0].c or 0) > 0

        log_run(
            bq_client,
            status="success",
            window_start=start_date,
            window_end=end_date,
            rows_raw_loaded=rows_raw_loaded,
            rows_curated_merged=rows_curated_loaded,
            rows_media_loaded=rows_media_loaded,
            rows_media_insights_lifetime_loaded=rows_media_insights_lifetime_loaded,
            landed_yesterday=landed_yesterday,
            error=None,
        )

        logger.info(
            "DONE raw=%s curated=%s media=%s media_lifetime=%s",
            rows_raw_loaded,
            rows_curated_loaded,
            rows_media_loaded,
            rows_media_insights_lifetime_loaded,
        )

    except Exception as exc:
        logger.exception("Instagram pipeline failed")
        log_run(
            bq_client,
            status="failed",
            window_start=None,
            window_end=None,
            rows_raw_loaded=rows_raw_loaded,
            rows_curated_merged=rows_curated_loaded,
            rows_media_loaded=rows_media_loaded,
            rows_media_insights_lifetime_loaded=rows_media_insights_lifetime_loaded,
            landed_yesterday=None,
            error=str(exc)[:4000],
        )
        raise


if __name__ == "__main__":
    main()