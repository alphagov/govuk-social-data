import argparse
import logging
from datetime import date, datetime, timedelta, timezone

from instagram.api import ACCOUNT_DAY_METRICS, ACCOUNT_TOTAL_VALUE_METRICS, MEDIA_LIFETIME_METRICS, build_client
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
from src.bigquery_upload_helpers import upload_partitioned
from src.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Instagram Graph API analytics pipeline")
    parser.add_argument(
        "--target-date",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Target date (YYYY-MM-DD). Defaults to yesterday.",
    )
    parser.add_argument(
        "--dataset",
        default="govuk_instagram",
        help="BigQuery dataset name to upload tables into (default: govuk_instagram).",
    )
    parser.add_argument(
        "--media-limit",
        type=int,
        default=None,
        help="Limit number of media items fetched (e.g. --media-limit 10). Defaults to all.",
    )
    parser.add_argument(
        "--run-media-insights",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch per-media insights (default: on). Use --no-run-media-insights to skip.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_date = date.fromisoformat(args.target_date)
    ingestion_timestamp = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    logger.info(
        "Target date: %s | Ingestion timestamp: %s | Dataset: %s",
        target_date,
        ingestion_timestamp,
        args.dataset,
    )

    client, ig_user_id = build_client()
    logger.info("Using IG user ID: %s", ig_user_id)

    # Account info
    logger.info("Fetching account info...")
    account = client.get_account(ig_user_id)
    upload_partitioned(
        account_to_arrow(account, ig_user_id),
        "instagram_account",
        ACCOUNT_SCHEMA,
        args.dataset,
        target_date,
        ingestion_timestamp,
    )

    # Account insights
    logger.info("Fetching account insights for %s...", target_date)
    day_metrics = client.get_account_insights(ig_user_id, target_date, target_date, ACCOUNT_DAY_METRICS)
    total_value_metrics = client.get_account_total_value_insights(
        ig_user_id, target_date, target_date, ACCOUNT_TOTAL_VALUE_METRICS
    )
    upload_partitioned(
        account_insights_to_arrow(ig_user_id, day_metrics + total_value_metrics),
        "instagram_account_insights",
        ACCOUNT_INSIGHTS_SCHEMA,
        args.dataset,
        target_date,
        ingestion_timestamp,
    )

    # Media
    logger.info("Fetching media%s...", f" (limit {args.media_limit})" if args.media_limit else "")
    media = client.get_media(ig_user_id, max_results=args.media_limit)
    logger.info("Found %d media items", len(media))
    upload_partitioned(
        media_to_arrow(ig_user_id, media),
        "instagram_media",
        MEDIA_SCHEMA,
        args.dataset,
        target_date,
        ingestion_timestamp,
    )

    # Media insights
    if args.run_media_insights:
        logger.info("Fetching insights for %d media items...", len(media))
        media_with_insights = []
        for i, m in enumerate(media, start=1):
            logger.info("Media %d/%d: %s", i, len(media), m.id)
            media_with_insights.append(client.get_media_insights(m, MEDIA_LIFETIME_METRICS))

        # upload_partitioned already no-ops on an empty table.
        upload_partitioned(
            media_insights_to_arrow(media_with_insights),
            "instagram_media_insights",
            MEDIA_INSIGHTS_SCHEMA,
            args.dataset,
            target_date,
            ingestion_timestamp,
        )
    else:
        logger.info("Skipping media insights (--no-run-media-insights)")


if __name__ == "__main__":
    main()
