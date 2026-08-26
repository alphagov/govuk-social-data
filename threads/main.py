"""Threads ingestion pipeline: fetch → convert → upload_partitioned, in two run modes.

The Threads API splits cleanly in two, which is what the modes are:

  daily     time-windowed metrics (views, likes, replies, reposts, quotes) partitioned on
            --target-date, the day they describe.
  snapshot  everything with no since/until support — profile, posts, post insights, replies,
            followers_count, demographics — partitioned on --snapshot-date, the day it was read.

Run:
    python -m threads.main --mode daily --target-date 2026-08-19 --dataset govuk_threads
    python -m threads.main --mode snapshot --dataset govuk_threads
"""

import argparse
import logging
from datetime import date, datetime, timedelta, timezone

from src.bigquery_upload_helpers import upload_partitioned
from src.meta.insights import run_insight_fetch
from threads.api import InsightMetricError, PaginationError, build_client
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Threads Graph API analytics pipeline")
    parser.add_argument(
        "--mode",
        choices=["snapshot", "daily"],
        default="daily",
        help="daily: time-windowed account metrics. snapshot: everything with no since/until support.",
    )
    parser.add_argument(
        "--target-date",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Daily mode only: the day to fetch, and its partition (YYYY-MM-DD). Defaults to yesterday.",
    )
    parser.add_argument(
        "--snapshot-date",
        default=date.today().isoformat(),
        help="Snapshot mode only: the partition to write today's reading into. Defaults to today.",
    )
    parser.add_argument(
        "--dataset",
        default="govuk_threads",
        help="BigQuery dataset name to upload tables into (default: govuk_threads).",
    )
    parser.add_argument(
        "--media-limit",
        type=int,
        default=None,
        help="Limit number of posts fetched (e.g. --media-limit 10). Defaults to all.",
    )
    parser.add_argument(
        "--reply-limit",
        type=int,
        default=None,
        help="Limit number of replies fetched (e.g. --reply-limit 10). Defaults to all.",
    )
    parser.add_argument(
        "--run-media-insights",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch per-post insights (default: on). Use --no-run-media-insights to skip.",
    )
    parser.add_argument(
        "--run-replies",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch replies (default: on). Use --no-run-replies to skip.",
    )
    parser.add_argument(
        "--run-demographics",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch follower demographics (default: on). Use --no-run-demographics to skip.",
    )
    return parser.parse_args()


def run_daily(client, threads_user_id: str, args, partition_date: date, ingestion_timestamp: datetime) -> None:
    """Time-windowed account metrics for one day.

    An unavailable metric degrades the run rather than aborting it: InsightMetricError carries the
    metrics that did succeed, and those are uploaded. An expired token is not an InsightMetricError
    and still crashes, which is the point of catching this narrowly.
    """
    logger.info("Fetching account insights for %s...", partition_date)
    try:
        metrics = client.get_account_insights(threads_user_id, partition_date, partition_date)
    except InsightMetricError as e:
        logger.error("Account insights partially failed: %s", e)
        metrics = e.partial_result or []

    upload_partitioned(
        account_insights_to_arrow(threads_user_id, metrics),
        "threads_account_insights",
        ACCOUNT_INSIGHTS_SCHEMA,
        args.dataset,
        partition_date,
        ingestion_timestamp,
    )

def upload_profile(client, threads_user_id, PROFILE_SCHEMA, args, partition_date, ingestion_timestamp):
    logger.info("Fetching profile...")
    profile = client.get_profile(threads_user_id)
    upload_partitioned(
        profile_to_arrow(profile, threads_user_id),
        "threads_profile",
        PROFILE_SCHEMA,
        args.dataset,
        partition_date,
        ingestion_timestamp
    )

def upload_media(client, threads_user_id, MEDIA_SCHEMA, args, partition_date, ingestion_timestamp,):
    # Posts
    logger.info("Fetching posts%s...", f" (limit {args.media_limit})" if args.media_limit else "")
    try:
        media = client.get_media(threads_user_id, max_results=args.media_limit)
    except PaginationError as e:
        logger.error("Media pagination failed: %s", e)
        media = e.partial_result
    logger.info("Found %d posts", len(media))
    if args.run_media_insights:
            logger.info("Fetching insights for %d posts...", len(media))
            media_with_insights = run_insight_fetch(media, client.get_media_insights, lambda m: m.id, "Media")
            upload_partitioned(
                media_to_arrow(threads_user_id,media_with_insights),
                "threads_media_insights",
                MEDIA_SCHEMA,
                args.dataset,
                partition_date,
                ingestion_timestamp,
            )
    else:
        logger.info("Skipping post insights (--no-run-media-insights)")

def upload_insights(client, threads_user_id, MEDIA_INSIGHTS_SCHEMA, args, partition_date, ingestion_timestamp):
    try:
        media = client.get_media(threads_user_id, max_results=args.media_limit)
    except PaginationError as e:
        logger.error("Media pagination failed: %s", e)
        media = e.partial_result
    logger.info("Fetching insights for %d posts...", len(media))
    media_with_insights = run_insight_fetch(media, client.get_media_insights, lambda m: m.id, "Media")
    if media_with_insights:
        upload_partitioned(
            media_insights_to_arrow(threads_user_id),
            "threads_profile",
            MEDIA_INSIGHTS_SCHEMA,
            args.dataset,
            partition_date,
            ingestion_timestamp
        )
    else:
        logger.info("Skipping post insights (--no-run-media-insights)")


def upload_replies(client, threads_user_id, replies_schema, args, partition_date, ingestion_timestamp):
    # Replies
    if args.run_replies:
        logger.info("Fetching replies%s...", f" (limit {args.reply_limit})" if args.reply_limit else "")
        try:
            replies = client.get_replies(threads_user_id, max_results=args.reply_limit)
        except PaginationError as e:
            logger.error("Reply pagination failed: %s", e)
            replies = e.partial_result
        logger.info("Found %d replies", len(replies))
        upload_partitioned(
            replies_to_arrow(threads_user_id, replies),
            "threads_replies",
            replies_schema,
            args.dataset,
            partition_date,
            ingestion_timestamp,
        )
    else:
        logger.info("Skipping replies (--no-run-replies)")

def upload_lifetime(client, threads_user_id, account_lifetime_schema, args, partition_date, ingestion_timestamp):
    logger.info("Fetching lifetime account metrics...")
    try:
        lifetime = client.get_account_lifetime_insights(threads_user_id)
    except InsightMetricError as e:
        logger.error("Lifetime account metrics partially failed: %s", e)
        lifetime = e.partial_result or []
    upload_partitioned(
        account_lifetime_to_arrow(threads_user_id, lifetime),
        "threads_account_lifetime",
        account_lifetime_schema,
        args.dataset,
        partition_date,
        ingestion_timestamp,
    )

def upload_demographics(client, threads_user_id, follower_demographics_schema, args, partition_date, ingestion_timestamp):
    if args.run_demographics:
        logger.info("Fetching follower demographics...")
        demographics = client.get_follower_demographics(threads_user_id)
        logger.info("Found %d demographic rows", len(demographics))
        upload_partitioned(
            follower_demographics_to_arrow(threads_user_id, demographics),
            "threads_follower_demographics",
            follower_demographics_schema,
            args.dataset,
            partition_date,
            ingestion_timestamp,
        )
    else:
        logger.info("Skipping follower demographics (--no-run-demographics)")

def run_snapshot(client, threads_user_id: str, args, partition_date: date, ingestion_timestamp: datetime) -> None:
    """Everything the API only reports as of now, written into the partition for today."""

    steps = {
        "profile": lambda: upload_profile(
            client,
            threads_user_id,
            PROFILE_SCHEMA,
            args,
            partition_date,
            ingestion_timestamp),
        "posts": lambda: upload_media(
            client,
            threads_user_id,
            MEDIA_SCHEMA,
            args,
            partition_date,
            ingestion_timestamp,
        ),
        "posts_insights": lambda:upload_insights(
            client,
            threads_user_id,
            MEDIA_INSIGHTS_SCHEMA,
            args,
            partition_date,
            ingestion_timestamp),
        "replies": lambda: upload_replies(
            client,
            threads_user_id,
            REPLIES_SCHEMA,
            args,
            partition_date,
            ingestion_timestamp,
        ),
        "lifetime_metrics": lambda: upload_partitioned(
            client, 
            threads_user_id,
            ACCOUNT_LIFETIME_SCHEMA,
            args,
            partition_date,
            ingestion_timestamp,
    ),
        "demographics": lambda: upload_partitioned(
            client,
            threads_user_id,
            FOLLOWER_DEMOGRAPHICS_SCHEMA,
            args,
            partition_date,
            ingestion_timestamp,
        ),

    }

    failed = []
    for name, step in steps.items():
        try:
            step()
        except Exception as e: 
            logger.error(f"error message for step {name}")
            failed.append(name)

    if failed:
        logger.warning("Missing today's partitin for: %s", ", ".join(failed))



    # Follower demographics
    if args.run_demographics:
        logger.info("Fetching follower demographics...")
        demographics = client.get_follower_demographics(threads_user_id)
        logger.info("Found %d demographic rows", len(demographics))
        upload_partitioned(
            follower_demographics_to_arrow(threads_user_id, demographics),
            "threads_follower_demographics",
            FOLLOWER_DEMOGRAPHICS_SCHEMA,
            args.dataset,
            partition_date,
            ingestion_timestamp,
        )
    else:
        logger.info("Skipping follower demographics (--no-run-demographics)")


def main() -> None:
    args = parse_args()
    # Each mode partitions on the date that describes its data: the day measured for daily, the day
    # read for snapshot. The other flag is ignored rather than validated — passing both is harmless.
    partition_date = date.fromisoformat(args.target_date if args.mode == "daily" else args.snapshot_date)
    ingestion_timestamp = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    logger.info(
        "Mode: %s | Partition date: %s | Ingestion timestamp: %s | Dataset: %s",
        args.mode,
        partition_date,
        ingestion_timestamp,
        args.dataset,
    )

    client, threads_user_id = build_client()
    logger.info("Using Threads user ID: %s", threads_user_id)

    if args.mode == "daily":
        run_daily(client, threads_user_id, args, partition_date, ingestion_timestamp)
    else:
        run_snapshot(client, threads_user_id, args, partition_date, ingestion_timestamp)


if __name__ == "__main__":
    main()
