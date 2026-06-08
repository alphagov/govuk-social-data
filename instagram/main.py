import argparse
import logging
from datetime import datetime, timedelta

from instagram.api import ACCOUNT_DAY_METRICS, ACCOUNT_TOTAL_VALUE_METRICS, MEDIA_LIFETIME_METRICS, build_client
from instagram.schemas import account_insights_to_arrow, account_to_arrow, media_insights_to_arrow, media_to_arrow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Instagram Graph API analytics pipeline")
    parser.add_argument(
        "--start-date",
        default=(datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="Start date (YYYY-MM-DD). Defaults to yesterday.",
    )
    parser.add_argument(
        "--end-date",
        default=(datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="End date (YYYY-MM-DD). Defaults to yesterday.",
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
    logger.info("Date range: %s to %s", args.start_date, args.end_date)

    from datetime import date

    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)

    client, ig_user_id = build_client()
    logger.info("Using IG user ID: %s", ig_user_id)

    # Account info
    logger.info("Fetching account info...")
    account = client.get_account(ig_user_id)
    account_table = account_to_arrow(account, ig_user_id)
    print("\n=== Account Info ===")
    print(account_table.to_pydict())

    # Account insights — day metrics
    logger.info("Fetching account insights (%s → %s)...", args.start_date, args.end_date)
    day_metrics = client.get_account_insights(ig_user_id, start_date, end_date, ACCOUNT_DAY_METRICS)
    total_value_metrics = client.get_account_total_value_insights(
        ig_user_id, start_date, end_date, ACCOUNT_TOTAL_VALUE_METRICS
    )
    all_metrics = day_metrics + total_value_metrics
    insights_table = account_insights_to_arrow(ig_user_id, all_metrics)
    print(f"\n=== Account Insights ({len(insights_table)} rows) ===")
    print(insights_table.to_pydict())

    # Media
    logger.info(
        "Fetching media%s...", f" (limit {args.media_limit})" if args.media_limit else ""
    )
    media = client.get_media(ig_user_id, max_results=args.media_limit)
    logger.info("Found %d media items", len(media))
    media_table = media_to_arrow(ig_user_id, media)
    print(f"\n=== Media ({len(media_table)} rows) ===")
    print(media_table.to_pydict())

    # Media insights
    if args.run_media_insights:
        logger.info("Fetching insights for %d media items...", len(media))
        all_media_insights = []
        for i, m in enumerate(media, start=1):
            logger.info("Media %d/%d: %s", i, len(media), m.id)
            insights = client.get_media_insights(m.id, MEDIA_LIFETIME_METRICS)
            if insights:
                insight_table = media_insights_to_arrow(m.id, insights)
                all_media_insights.append(insight_table)

        if all_media_insights:
            import pyarrow as pa

            combined = pa.concat_tables(all_media_insights)
            print(f"\n=== Media Insights ({len(combined)} rows) ===")
            print(combined.to_pydict())
        else:
            print("\n=== Media Insights (0 rows) ===")
    else:
        logger.info("Skipping media insights (--no-run-media-insights)")


if __name__ == "__main__":
    main()
