import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

from facebook.api import PAGE_ID_SECRET, PROJECT_ID, build_client
from facebook.schemas import (
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Facebook Graph API analytics pipeline")
    parser.add_argument(
        "--start-date",
        default=(datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="Start date (YYYY-MM-DD). Defaults to 1 days ago.",
    )
    parser.add_argument(
        "--end-date",
        default=(datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="End date (YYYY-MM-DD). Defaults to one day ago.",
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


def main() -> None:
    args = parse_args()
    logger.info("Date range: %s to %s", args.start_date, args.end_date)

    client = build_client()
    page_id = get_gcp_secret(PAGE_ID_SECRET, project_id=PROJECT_ID)

    # Page info
    logger.info("Fetching page info...")
    page = client.get_page(page_id)
    page_info_table = page_info_to_arrow(page)
    print("\n=== Page Info ===")
    print(page_info_table.to_pydict())

    # Page insights
    logger.info("Fetching page insights (%s → %s)...", args.start_date, args.end_date)
    page_insights = client.get_page_insights(page_id, since=args.start_date, until=args.end_date)
    page_insights_table = page_insights_to_arrow(page_id, page_insights)
    print(f"\n=== Page Insights ({len(page_insights_table)} rows) ===")
    print(page_insights_table.to_pydict())

    # Posts
    if args.run_post_data:
        logger.info("Fetching posts%s...", f" (limit {args.post_limit})" if args.post_limit else "")
        posts = client.get_posts(page_id, limit=args.post_limit, max_results=args.post_limit)
        logger.info("Found %d posts", len(posts))
        posts_table = posts_to_arrow(posts)
        print(f"\n=== Posts ({len(posts_table)} rows) ===")
        print(posts_table.to_pydict())

        if args.run_post_insights:
            logger.info("Fetching insights for %d posts...", len(posts))
            posts_with_insights = []
            for i, post in enumerate(posts, start=1):
                logger.info("Post %d/%d: %s", i, len(posts), post.post_id)
                posts_with_insights.append(client.get_post_insights(post))

            post_insights_table = post_insights_to_arrow(posts_with_insights)
            print(f"\n=== Post Insights ({len(post_insights_table)} rows) ===")
            print(post_insights_table.to_pydict())

            post_reactions_table = post_reactions_to_arrow(posts_with_insights)
            print(f"\n=== Post Reactions ({len(post_reactions_table)} rows) ===")
            print(post_reactions_table.to_pydict())

            post_activity_table = post_activity_to_arrow(posts_with_insights)
            print(f"\n=== Post Activity by Action Type ({len(post_activity_table)} rows) ===")
            print(post_activity_table.to_pydict())

            post_clicks_table = post_clicks_by_type_to_arrow(posts_with_insights)
            print(f"\n=== Post Clicks by Type ({len(post_clicks_table)} rows) ===")
            print(post_clicks_table.to_pydict())
        else:
            logger.info("Skipping post insights (--no-run-post-insights)")
    else:
        logger.info("Skipping all post data (--no-run-post-data)")

    # Videos
    if args.run_video_data:
        logger.info("Fetching videos%s...", f" (limit {args.video_limit})" if args.video_limit else "")
        videos = client.get_videos(page_id, limit=args.video_limit, max_results=args.video_limit)
        logger.info("Found %d videos", len(videos))
        videos_table = videos_to_arrow(videos)
        print(f"\n=== Videos ({len(videos_table)} rows) ===")
        print(videos_table.to_pydict())

        if args.run_video_insights:
            logger.info("Fetching insights for %d videos...", len(videos))
            videos_with_insights = []
            for i, video in enumerate(videos, start=1):
                logger.info("Video %d/%d: %s", i, len(videos), video.video_id)
                videos_with_insights.append(client.get_video_insights(video))

            video_insights_table = video_insights_to_arrow(videos_with_insights)
            print(f"\n=== Video Insights — scalar ({len(video_insights_table)} rows) ===")
            print(video_insights_table.to_pydict())

            video_reactions_table = video_reel_reactions_to_arrow(videos_with_insights)
            print(f"\n=== Video Reel Reactions ({len(video_reactions_table)} rows) ===")
            print(video_reactions_table.to_pydict())

            video_social_table = video_reel_social_actions_to_arrow(videos_with_insights)
            print(f"\n=== Video Reel Social Actions ({len(video_social_table)} rows) ===")
            print(video_social_table.to_pydict())

            video_retention_table = video_reel_retention_to_arrow(videos_with_insights)
            print(f"\n=== Video Reel Retention ({len(video_retention_table)} rows) ===")
            print(video_retention_table.to_pydict())
        else:
            logger.info("Skipping video insights (--no-run-video-insights)")
    else:
        logger.info("Skipping all video data (--no-run-video-data)")


if __name__ == "__main__":
    main()
