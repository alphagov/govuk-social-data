import json
import logging
import time
from datetime import datetime

import requests

from facebook.models import (
    FacebookReelInsights,
    Page,
    PageInsightMetric,
    PageInsightValue,
    Post,
    PostInsightMetric,
    Video,
)
from src.authentication import FileTokenCache, get_gcp_secret

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
    "post_impressions_unique",
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
REEL_SCALAR_METRICS = {
    "video_id", 
    "upload_url", 
    "success", 
    "message", 
    "post_id" 
}

REEL_DICT_METRICS = {}


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------


def _refresh_access_token(app_id: str, app_secret: str, long_lived_token: str) -> str:
    response = requests.get(
        f"{FB_GRAPH_BASE}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": long_lived_token,
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    access_token = data["access_token"]
    expires_in = data.get("expires_in")
    expires_at = None
    if expires_in:
        expires_at = datetime.fromtimestamp(datetime.now().timestamp() + expires_in).isoformat()
    _FACEBOOK_TOKEN_CACHE.save({"access_token": access_token, "expires_at": expires_at})
    logger.info("Refreshed Facebook access token (expires_in=%s seconds)", expires_in)
    return access_token


def build_client() -> "FacebookClient":
    cached = _FACEBOOK_TOKEN_CACHE.load_valid()
    if cached:
        logger.info("Using cached Facebook access token")
        return FacebookClient(access_token=cached["access_token"])

    app_id = get_gcp_secret(APP_ID_SECRET)
    app_secret = get_gcp_secret(APP_SECRET_SECRET)
    long_lived_token = json.loads(get_gcp_secret(TOKEN_JSON_SECRET))["access_token"]

    try:
        access_token = _refresh_access_token(app_id, app_secret, long_lived_token)
    except Exception:
        logger.warning("Token refresh failed, retrying with latest secret version")
        long_lived_token = json.loads(get_gcp_secret(TOKEN_JSON_SECRET, "latest"))["access_token"]
        access_token = _refresh_access_token(app_id, app_secret, long_lived_token)

    return FacebookClient(access_token=access_token)


# ------------------------------------------------------------------
# API client
# ------------------------------------------------------------------


class FacebookClient:
    def __init__(self, access_token: str) -> None:
        self._session = requests.Session()
        self._session.params = {"access_token": access_token}  # type: ignore[assignment]

    def _get(self, path: str, params: dict | None = None, retries: int = 3) -> dict:
        url = f"{FB_GRAPH_BASE}/{path.lstrip('/')}"
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
            paging = data.get("paging", {})
            after = paging.get("cursors", {}).get("after")
            if not after or not paging.get("next"):
                break
            data = self._get(path, {**(params or {}), "after": after})
            results.extend(data.get("data", []))
        return results[:max_results] if max_results is not None else results

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

    def _fetch_insight_items(self, path: str, metric_names: list[str], params: dict) -> list[dict]:
        """Fetches insight items, falling back to per-metric requests on a 400.

        The Graph API rejects the entire batch if any single metric is unavailable
        for the page (e.g. follower metrics require specific permissions). Retrying
        individually lets us collect what is accessible and skip what is not.
        """
        try:
            data = self._get(path, {**params, "metric": ",".join(metric_names)})
            return data.get("data", [])
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 400:
                raise
            logger.warning("Batch insights request returned 400, retrying per metric")

        items: list[dict] = []
        skipped: list[str] = []
        for metric in metric_names:
            try:
                data = self._get(path, {**params, "metric": metric})
                items.extend(data.get("data", []))
            except requests.HTTPError:
                skipped.append(metric)
        if skipped:
            logger.warning("Skipped %d unavailable metric(s): %s", len(skipped), ", ".join(skipped))
        return items

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

    def get_posts(self, page_id: str, limit: int | None = None, max_results: int | None = None) -> list[Post]:
        params: dict = {
            "fields": f"id,message,created_time,permalink_url,attachments{{type}},{_REACTION_FIELDS}"
        }
        if limit is not None:
            params["limit"] = limit
        raw_posts = self._paginate(f"{page_id}/posts", params=params, max_results=max_results)
        posts: list[Post] = []
        for raw in raw_posts:
            post_type = "status"
            attachments = raw.get("attachments", {}).get("data", [])
            if attachments:
                post_type = attachments[0].get("type", "status")
            reactions = {
                "LIKE":  raw.get("like_reactions",  {}).get("summary", {}).get("total_count", 0),
                "LOVE":  raw.get("love_reactions",  {}).get("summary", {}).get("total_count", 0),
                "WOW":   raw.get("wow_reactions",   {}).get("summary", {}).get("total_count", 0),
                "HAHA":  raw.get("haha_reactions",  {}).get("summary", {}).get("total_count", 0),
                "SAD":   raw.get("sad_reactions",   {}).get("summary", {}).get("total_count", 0),
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

    # ------------------------------------------------------------------
    # Page/videos edge
    # ------------------------------------------------------------------

    def get_videos(self, page_id: str, limit: int | None = None, max_results: int | None = None) -> list[Video]:
        params: dict = {"fields": "id,title,description,created_time,length,post_id"}
        if limit is not None:
            params["limit"] = limit
        raw_videos = self._paginate(f"{page_id}/videos", params=params, max_results=max_results)
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

    # ------------------------------------------------------------------
    # Video/video_insights edge
    # ------------------------------------------------------------------

    def get_video_insights(self, video: Video) -> Video:
        try:
            data = self._get(
                f"{video.video_id}/video_insights",
                params={"metric": ",".join(VIDEO_METRICS), "period": "lifetime"},
            )
        except requests.HTTPError as e:
            logger.warning("Could not fetch insights for video %s: %s", video.video_id, e)
            return video
        return video.model_copy(update={"insights": FacebookReelInsights.model_validate(data)})

    # ------------------------------------------------------------------
    # Post/insights edge
    # ------------------------------------------------------------------

    def get_post_insights(self, post: Post) -> Post:
        try:
            data = self._get(
                f"{post.post_id}/insights",
                params={"metric": ",".join(POST_METRICS), "period": "lifetime"},
            )
        except requests.HTTPError as e:
            logger.warning("Could not fetch insights for post %s: %s", post.post_id, e)
            return post

        insights: list[PostInsightMetric] = []
        for item in data.get("data", []):
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
        return post.model_copy(update={"insights": insights})
