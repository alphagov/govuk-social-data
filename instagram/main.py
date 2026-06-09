import argparse
import logging
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa

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
from src.bigquery_upload_helpers import load_data_to_partitioned_bq

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DATASET = "govuk_instagram"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Instagram Graph API analytics pipeline")
    parser.add_argument(
        "--target-date",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Target date (YYYY-MM-DD). Defaults to yesterday.",
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
    ingestion_ts = datetime.now(timezone.utc)
    logger.info("Target date: %s | Ingestion timestamp: %s", target_date, ingestion_ts)

    client, ig_user_id = build_client()
    logger.info("Using IG user ID: %s", ig_user_id)

    # Account info
    logger.info("Fetching account info...")
    account = client.get_account(ig_user_id)
    account_table = account_to_arrow(account, ig_user_id, target_date, ingestion_ts)
    rows = load_data_to_partitioned_bq(account_table, "instagram_account", DATASET, ACCOUNT_SCHEMA, "target_date")
    logger.info("Loaded %d rows to instagram_account", rows)

    # Account insights
    logger.info("Fetching account insights for %s...", target_date)
    day_metrics = client.get_account_insights(ig_user_id, target_date, target_date, ACCOUNT_DAY_METRICS)
    total_value_metrics = client.get_account_total_value_insights(
        ig_user_id, target_date, target_date, ACCOUNT_TOTAL_VALUE_METRICS
    )
    insights_table = account_insights_to_arrow(ig_user_id, day_metrics + total_value_metrics, target_date, ingestion_ts)
    rows = load_data_to_partitioned_bq(
        insights_table, "instagram_account_insights", DATASET, ACCOUNT_INSIGHTS_SCHEMA, "target_date"
    )
    logger.info("Loaded %d rows to instagram_account_insights", rows)

    # Media
    logger.info("Fetching media%s...", f" (limit {args.media_limit})" if args.media_limit else "")
    media = client.get_media(ig_user_id, max_results=args.media_limit)
    logger.info("Found %d media items", len(media))
    media_table = media_to_arrow(ig_user_id, media, target_date, ingestion_ts)
    rows = load_data_to_partitioned_bq(media_table, "instagram_media", DATASET, MEDIA_SCHEMA, "target_date")
    logger.info("Loaded %d rows to instagram_media", rows)

    # Media insights
    if args.run_media_insights:
        logger.info("Fetching insights for %d media items...", len(media))
        all_media_insights = []
        for i, m in enumerate(media, start=1):
            logger.info("Media %d/%d: %s", i, len(media), m.id)
            insights = client.get_media_insights(m.id, MEDIA_LIFETIME_METRICS)
            if insights:
                all_media_insights.append(media_insights_to_arrow(m.id, insights, target_date, ingestion_ts))

        if all_media_insights:
            combined = pa.concat_tables(all_media_insights)
            rows = load_data_to_partitioned_bq(
                combined, "instagram_media_insights", DATASET, MEDIA_INSIGHTS_SCHEMA, "target_date"
            )
            logger.info("Loaded %d rows to instagram_media_insights", rows)
        else:
            logger.info("No media insights to load")
    else:
        logger.info("Skipping media insights (--no-run-media-insights)")


if __name__ == "__main__":
    main()
