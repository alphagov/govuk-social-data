"""
The purpose of this API file is to access the endpoints of the facebook graph API, which are
structured via the node/edge attribute structure.

The rough call structure is, with _paginate, _fetch_insight_items and _get all inherited from
src.meta.client.GraphClient rather than defined here:

Facebook client - (account Node)
|
|
|-- get_videos           _paginate              _get
|-- get_posts            _paginate              _get
|-- get_page                                    _get
|-- get_page_insights    _fetch_insight_items   _get
|-- get_video_insights   _fetch_insight_items   _get
|-- get_post_insights    _fetch_insight_items   _get


Graph structure

Node    Edge            Node        Edge
- Page
        - videos        -Page       - insights
        - posts         -Video      - insights
        - insights
"""

import logging

from facebook.models import (
    FacebookReelInsights,
    Page,
    PageInsightMetric,
    PageInsightValue,
    Post,
    PostInsightMetric,
    Video,
)
from src.authentication import FileTokenCache
from src.meta.auth import get_graph_token
from src.meta.client import GraphClient

# Re-exported: facebook/main.py and tests/test_facebook.py import these from here, and the
# top-level fetch functions below catch them.
from src.meta.errors import InsightMetricError, PaginationError  # noqa: F401

logger = logging.getLogger(__name__)

FB_GRAPH_BASE = "https://graph.facebook.com/v25.0"

PROJECT_ID = "gds-social-data"
APP_ID_SECRET = "facebook_app_id"
APP_SECRET_SECRET = "facebook_app_secret"
TOKEN_JSON_SECRET = "facebook_page_token"
PAGE_ID_SECRET = "facebook_page_id"

_FACEBOOK_TOKEN_CACHE = FileTokenCache("~/.config/facebook_exploration/token_cache.json")

PAGE_DAY_METRICS = [
    # page_impressions deprecated Nov 2025 → page_media_view
    # page_impressions_unique deprecated Jun 2025 → page_total_media_view_unique
    # page_fan_adds / page_fan_removes deprecated Nov 2025 (no replacement)
    "page_media_view",
    "page_total_media_view_unique",
    "page_post_engagements",
    "page_daily_follows_unique",
    "page_daily_unfollows_unique",
    "page_views_total",
    "page_actions_post_reactions_total",
]
PAGE_LIFETIME_METRICS = [
    # page_fans deprecated Nov 2025 → page_follows
    "page_follows",
]

POST_METRICS = [
    # post_impressions deprecated Nov 2025 → post_media_view
    # post_impressions_unique deprecated Jun 2025 → post_total_media_view_unique
    # post_engagements — accepted (HTTP 200) but always returns empty data; omitted
    # post_reactions_by_type_total deprecated/broken — always returns {} — reactions now via /reactions edge
    "post_media_view",
    "post_total_media_view_unique",
    "post_activity_by_action_type_unique",  # dict: {action_type: unique_users}
    "post_clicks",
    "post_clicks_by_type",  # dict: {click_type: count}
]

_REACTION_FIELDS = (
    "reactions.type(LIKE).limit(0).summary(total_count).as(like_reactions),"
    "reactions.type(LOVE).limit(0).summary(total_count).as(love_reactions),"
    "reactions.type(WOW).limit(0).summary(total_count).as(wow_reactions),"
    "reactions.type(HAHA).limit(0).summary(total_count).as(haha_reactions),"
    "reactions.type(SAD).limit(0).summary(total_count).as(sad_reactions),"
    "reactions.type(ANGRY).limit(0).summary(total_count).as(angry_reactions)"
)

VIDEO_SCALAR_METRICS = [
    "post_video_avg_time_watched",
    "post_video_view_time",
    # post_impressions_unique rejected by the /video_insights edge as of the Jun 15 2026 Meta
    # metric deprecation wave, which retires unique-video-view metrics with no direct replacement.
    # post_total_media_view_unique (the general Page/Post-level remap for this wave) is the closest replacement
    # for meta products going forward. Re-add if Meta
    # documents a specific Reels reach metric.
    "blue_reels_play_count",
    "fb_reels_total_plays",
    "fb_reels_replay_count",
    "post_video_followers",
]
VIDEO_DICT_METRICS = [
    "post_video_likes_by_reaction_type",
    "post_video_social_actions",
    "post_video_retention_graph",
]
VIDEO_METRICS = VIDEO_SCALAR_METRICS + VIDEO_DICT_METRICS

# video_reels object
REEL_SCALAR_METRICS = {"video_id", "upload_url", "success", "message", "post_id"}

REEL_DICT_METRICS = {}


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------


def build_client() -> "FacebookClient":
    """Build a FacebookClient from the JSON-wrapped page token in Secret Manager."""
    return FacebookClient(
        access_token=get_graph_token(TOKEN_JSON_SECRET, json_wrapped=True, cache=_FACEBOOK_TOKEN_CACHE)
    )


# ------------------------------------------------------------------
# API client
# ------------------------------------------------------------------


class FacebookClient(GraphClient):
    """Graph API client for a Facebook Page.

    Transport (_get, _paginate, _fetch_insight_items) is inherited from GraphClient; this class
    supplies the base URL, the metric constants and the model builders.
    """

    BASE_URL = FB_GRAPH_BASE

    # ------------------------------------------------------------------
    # Page node
    # ------------------------------------------------------------------

    def get_page(self, page_id: str) -> Page:
        data = self._get(
            page_id,
            params={"fields": "id,name,fan_count,followers_count,talking_about_count"},
        )
        return Page(
            page_id=data["id"],
            name=data["name"],
            fan_count=data.get("fan_count"),
            followers_count=data.get("followers_count"),
            talking_about_count=data.get("talking_about_count"),
        )

    # ------------------------------------------------------------------
    # Page/insights edge
    # ------------------------------------------------------------------

    def get_page_insights(self, page_id: str, since: str, until: str) -> list[PageInsightMetric]:
        metrics: list[PageInsightMetric] = []

        day_items = self._fetch_insight_items(
            f"{page_id}/insights",
            PAGE_DAY_METRICS,
            {"period": "day", "since": since, "until": until},
        )
        lifetime_items = (
            self._fetch_insight_items(f"{page_id}/insights", PAGE_LIFETIME_METRICS, {"period": "lifetime"})
            if PAGE_LIFETIME_METRICS
            else []
        )

        for item in day_items + lifetime_items:
            metrics.append(
                PageInsightMetric(
                    name=item["name"],
                    period=item["period"],
                    values=[
                        PageInsightValue(end_time=v.get("end_time"), value=int(v["value"]))
                        for v in item.get("values", [])
                        if isinstance(v["value"], (int, float))
                    ],
                )
            )

        return metrics

    # ------------------------------------------------------------------
    # Page/posts edge
    # ------------------------------------------------------------------

    @staticmethod
    def _build_posts(raw_posts: list[dict], page_id: str) -> list[Post]:
        posts: list[Post] = []
        for raw in raw_posts:
            post_type = "status"
            attachments = raw.get("attachments", {}).get("data", [])
            if attachments:
                post_type = attachments[0].get("type", "status")
            reactions = {
                "LIKE": raw.get("like_reactions", {}).get("summary", {}).get("total_count", 0),
                "LOVE": raw.get("love_reactions", {}).get("summary", {}).get("total_count", 0),
                "WOW": raw.get("wow_reactions", {}).get("summary", {}).get("total_count", 0),
                "HAHA": raw.get("haha_reactions", {}).get("summary", {}).get("total_count", 0),
                "SAD": raw.get("sad_reactions", {}).get("summary", {}).get("total_count", 0),
                "ANGRY": raw.get("angry_reactions", {}).get("summary", {}).get("total_count", 0),
            }
            posts.append(
                Post(
                    post_id=raw["id"],
                    page_id=page_id,
                    message=raw.get("message"),
                    created_time=raw["created_time"],
                    post_type=post_type,
                    permalink_url=raw.get("permalink_url"),
                    reactions=reactions,
                )
            )
        return posts

    def get_posts(self, page_id: str, limit: int | None = None, max_results: int | None = None) -> list[Post]:
        params: dict = {"fields": f"id,message,created_time,permalink_url,attachments{{type}},{_REACTION_FIELDS}"}
        if limit is not None:
            params["limit"] = limit
        try:
            raw_posts = self._paginate(
                f"{page_id}/posts", params=params, max_results=max_results, raise_on_failure=True
            )
        except PaginationError as e:
            e.partial_result = self._build_posts(e.items, page_id)
            raise
        return self._build_posts(raw_posts, page_id)

    # ------------------------------------------------------------------
    # Page/videos edge
    # ------------------------------------------------------------------

    @staticmethod
    def _build_videos(raw_videos: list[dict], page_id: str) -> list[Video]:
        videos: list[Video] = []
        for raw in raw_videos:
            videos.append(
                Video(
                    video_id=raw["id"],
                    page_id=page_id,
                    associated_post_id=raw.get("post_id"),
                    title=raw.get("title"),
                    description=raw.get("description"),
                    created_time=raw["created_time"],
                    length=raw.get("length"),
                )
            )
        return videos

    def get_videos(self, page_id: str, limit: int | None = None, max_results: int | None = None) -> list[Video]:
        params: dict = {"fields": "id,title,description,created_time,length,post_id"}
        if limit is not None:
            params["limit"] = limit
        try:
            raw_videos = self._paginate(
                f"{page_id}/videos", params=params, max_results=max_results, raise_on_failure=True
            )
        except PaginationError as e:
            e.partial_result = self._build_videos(e.items, page_id)
            raise
        return self._build_videos(raw_videos, page_id)

    # ------------------------------------------------------------------
    # Video/video_insights edge
    # ------------------------------------------------------------------

    def get_video_insights(self, video: Video) -> Video:
        try:
            items = self._fetch_insight_items(
                f"{video.video_id}/video_insights", VIDEO_METRICS, {"period": "lifetime"}, raise_on_failure=True
            )
        except InsightMetricError as e:
            e.partial_result = video.model_copy(
                update={"insights": FacebookReelInsights.model_validate({"data": e.items})}
            )
            raise
        return video.model_copy(update={"insights": FacebookReelInsights.model_validate({"data": items})})

    # ------------------------------------------------------------------
    # Post/insights edge
    # ------------------------------------------------------------------

    @staticmethod
    def _build_post_insights(items: list[dict]) -> list[PostInsightMetric]:
        insights: list[PostInsightMetric] = []
        for item in items:
            if item.get("period") != "lifetime":
                continue
            values = item.get("values", [])
            raw_value = values[0]["value"] if values else 0
            insights.append(
                PostInsightMetric(
                    name=item["name"],
                    period=item["period"],
                    value=raw_value,
                )
            )
        return insights

    def get_post_insights(self, post: Post) -> Post:
        try:
            items = self._fetch_insight_items(
                f"{post.post_id}/insights", POST_METRICS, {"period": "lifetime"}, raise_on_failure=True
            )
        except InsightMetricError as e:
            e.partial_result = post.model_copy(update={"insights": self._build_post_insights(e.items)})
            raise
        return post.model_copy(update={"insights": self._build_post_insights(items)})
