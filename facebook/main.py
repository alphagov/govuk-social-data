import argparse
import logging
import os
import sys

from datetime import datetime, timedelta, timezone

import pyarrow as pa
import requests

from facebook.api import PAGE_ID_SECRET, PROJECT_ID, InsightMetricError, build_client
from facebook.schemas import (
    PAGE_INFO_SCHEMA,
    PAGE_INSIGHTS_SCHEMA,
    POST_ACTIVITY_SCHEMA,
    POST_CLICKS_BY_TYPE_SCHEMA,
    POST_INSIGHTS_SCHEMA,
    POST_REACTIONS_SCHEMA,
    POSTS_SCHEMA,
    VIDEO_INSIGHTS_SCHEMA,
    VIDEO_REEL_REACTIONS_SCHEMA,
    VIDEO_REEL_RETENTION_SCHEMA,
    VIDEO_REEL_SOCIAL_ACTIONS_SCHEMA,
    VIDEOS_SCHEMA,
    page_info_to_arrow,
    page_insights_to_arrow,
    post_activity_to_arrow,
    post_clicks_by_type_to_arrow,
    post_insights_to_arrow,
    post_reactions_to_arrow,
    posts_to_arrow,
    video_insights_to_arrow,
    video_reel_reactions_to_arrow,
    video_reel_retention_to_arrow,
    video_reel_social_actions_to_arrow,
    videos_to_arrow,
)
from src.authentication import get_gcp_secret
from src.bigquery_upload_helpers import upload_partitioned

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Facebook Graph API analytics pipeline")
    parser.add_argument(
        "--start-date",
        default=(datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="Start date (YYYY-MM-DD). Defaults to 1 day ago. Assumes start-date == end-date.",
    )
    parser.add_argument(
        "--end-date",
        default=datetime.today().strftime("%Y-%m-%d"),
        help="End date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--dataset",
        default="govuk_facebook",
        help="BigQuery dataset name to upload tables into (default: govuk_facebook).",
    )
    parser.add_argument(
        "--run-post-data",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch posts and post insights (default: on). Use --no-run-post-data to skip all post data.",
    )
    parser.add_argument(
        "--post-limit",
        type=int,
        default=None,
        help="Limit number of posts fetched (e.g. --post-limit 10). Defaults to all posts.",
    )
    parser.add_argument(
        "--run-post-insights",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch per-post insights (default: on). Use --no-run-post-insights to skip.",
    )
    parser.add_argument(
        "--run-video-data",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch videos and video insights (default: on). Use --no-run-video-data to skip all video data.",
    )
    parser.add_argument(
        "--video-limit",
        type=int,
        default=None,
        help="Limit number of videos fetched (e.g. --video-limit 10). Defaults to all videos.",
    )
    parser.add_argument(
        "--run-video-insights",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch per-video watch time/view insights (default: on). Use --no-run-video-insights to skip.",
    )
    return parser.parse_args()


def _run_insight_fetch(items: list, fetch_one, get_id, label: str) -> list:
    """
    Runs fetch_one(item) for each item, tolerating per-metric insight failures.
    
    On InsightMetricError, keeps the item's partial_result (every metric that DID
    succeed) instead of discarding its insights entirely, logs the specific metric(s)
    that failed, and tallies them for one consolidated summary line at the end instead
    of one generic warning per item.

        PARAMETERS

            items: items can be either a list of post ids or video_ids.
            fetch_one: a client object that fetches insights for the ids passed
            get_id: gets a string ID 
            label: video or post id


        RETURNS  
            A list of 


    """
    results = []
    failed_metrics: dict[str, int] = {}
    for i, item in enumerate(items, start=1):
        item_id = get_id(item)
        logger.info("%s %d/%d: %s", label, i, len(items), item_id)
        try:
            results.append(fetch_one(item))
        except InsightMetricError as e:
            logger.error("%s %s: %s", label, item_id, e)
            results.append(e.partial_result)
            for m in e.metrics:
                failed_metrics[m] = failed_metrics.get(m, 0) + 1
        except requests.HTTPError as e:
            logger.error("%s %s: %s", label, item_id, e)
            results.append(item)
    if failed_metrics:
        logger.warning(
            "%s insight metric failures across %d item(s): %s",
            label,
            len(items),
            ", ".join(f"{m} ({c})" for m, c in sorted(failed_metrics.items(), key=lambda kv: -kv[1])),
        )
    return results


def main() -> None:
    args = parse_args()
    target_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    ingestion_timestamp = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    logger.info(
        "Target date: %s | Ingestion timestamp: %s | Dataset: %s",
        target_date,
        ingestion_timestamp,
        args.dataset,
    )

    client = build_client()
    page_id = get_gcp_secret(PAGE_ID_SECRET, project_id=PROJECT_ID)

    # Page info
    logger.info("Fetching page info...")
    page = client.get_page(page_id)
    upload_partitioned(page_info_to_arrow(page), "facebook_page_info", PAGE_INFO_SCHEMA, args.dataset, target_date, ingestion_timestamp)

    # Page insights
    logger.info("Fetching page insights for %s...", args.start_date)
    page_insights = client.get_page_insights(page_id, since=args.start_date, until=args.end_date)
    upload_partitioned(
        page_insights_to_arrow(page_id, page_insights),
        "facebook_page_insights",
        PAGE_INSIGHTS_SCHEMA,
        args.dataset,
        target_date,
        ingestion_timestamp,
    )

    # Posts
    if args.run_post_data:
        logger.info("Fetching posts%s...", f" (limit {args.post_limit})" if args.post_limit else "")
        posts = client.get_posts(page_id, limit=args.post_limit, max_results=args.post_limit)
        logger.info("Found %d posts", len(posts))
        upload_partitioned(posts_to_arrow(posts), "facebook_posts", POSTS_SCHEMA, args.dataset, target_date, ingestion_timestamp)

        if args.run_post_insights:
            logger.info("Fetching insights for %d posts...", len(posts))
            posts_with_insights = _run_insight_fetch(
                posts, client.get_post_insights, lambda p: p.post_id, "Post"
            )

            upload_partitioned(post_insights_to_arrow(posts_with_insights), "facebook_post_insights", POST_INSIGHTS_SCHEMA, args.dataset, target_date, ingestion_timestamp)
            upload_partitioned(post_reactions_to_arrow(posts_with_insights), "facebook_post_reactions", POST_REACTIONS_SCHEMA, args.dataset, target_date, ingestion_timestamp)
            upload_partitioned(post_activity_to_arrow(posts_with_insights), "facebook_post_activity", POST_ACTIVITY_SCHEMA, args.dataset, target_date, ingestion_timestamp)
            upload_partitioned(post_clicks_by_type_to_arrow(posts_with_insights), "facebook_post_clicks_by_type", POST_CLICKS_BY_TYPE_SCHEMA, args.dataset, target_date, ingestion_timestamp)
        else:
            logger.info("Skipping post insights (--no-run-post-insights)")
    else:
        logger.info("Skipping all post data (--no-run-post-data)")

    # Videos
    if args.run_video_data:
        logger.info("Fetching videos%s...", f" (limit {args.video_limit})" if args.video_limit else "")
        videos = client.get_videos(page_id, limit=args.video_limit, max_results=args.video_limit)
        logger.info("Found %d videos", len(videos))
        upload_partitioned(videos_to_arrow(videos), "facebook_videos", VIDEOS_SCHEMA, args.dataset, target_date, ingestion_timestamp)

        if args.run_video_insights:
            logger.info("Fetching insights for %d videos...", len(videos))
            videos_with_insights = _run_insight_fetch(
                videos, client.get_video_insights, lambda v: v.video_id, "Video"
            )

            upload_partitioned(video_insights_to_arrow(videos_with_insights), "facebook_video_insights", VIDEO_INSIGHTS_SCHEMA, args.dataset, target_date, ingestion_timestamp)
            upload_partitioned(video_reel_reactions_to_arrow(videos_with_insights), "facebook_video_reel_reactions", VIDEO_REEL_REACTIONS_SCHEMA, args.dataset, target_date, ingestion_timestamp)
            upload_partitioned(video_reel_social_actions_to_arrow(videos_with_insights), "facebook_video_reel_social_actions", VIDEO_REEL_SOCIAL_ACTIONS_SCHEMA, args.dataset, target_date, ingestion_timestamp)
            upload_partitioned(video_reel_retention_to_arrow(videos_with_insights), "facebook_video_reel_retention", VIDEO_REEL_RETENTION_SCHEMA, args.dataset, target_date, ingestion_timestamp)
        else:
            logger.info("Skipping video insights (--no-run-video-insights)")
    else:
        logger.info("Skipping all video data (--no-run-video-data)")


if __name__ == "__main__":
    main()
 