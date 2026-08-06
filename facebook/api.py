"""
The purpose of this API file is to access the endpoints of the facebook graph API, which are structued via the node,
edge attribute structured. 


The rough call structure is 

Facebook client - (account Node)
|
|
|-- get_videos           _paginate              _get
|-- get_posts            _paginate              _get
|-- get_page                                    _get
|-- get_page_insights    _fetch_insight_items   _get
|-- get_video_insights   _fetch_insights_items   _get
|-- get_post_insights    _fetch_insight_items    _get


Graph structure 

Node    Edge            Node        Edge
- Page
        - videos        -Page       - insights
        - posts         -Video      - insights
        - insights
"""
from http import HTTPStatus
import json
import logging
import time
from datetime import datetime

import requests
from requests.exceptions import HTTPError

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


def build_client() -> "FacebookClient":
    cached = _FACEBOOK_TOKEN_CACHE.load_valid()
    if cached:
        logger.info("Using cached Facebook access token")
        return FacebookClient(access_token=cached["access_token"])

    try:
        access_token = json.loads(get_gcp_secret(TOKEN_JSON_SECRET))["access_token"]
    except Exception:
        logger.warning("Token refresh failed, retrying with latest secret version")
        access_token = json.loads(get_gcp_secret(TOKEN_JSON_SECRET, "latest"))["access_token"]

    return FacebookClient(access_token=access_token)


class InsightMetricError(RuntimeError):
    """Raised when one or more Graph API insight metrics fail. Carries the metrics that DID
    succeed (`items`) and, once the caller attaches it, the partially-populated result
    (`partial_result`) so failures never discard data for the metrics that worked."""

    def __init__(self, path: str, metrics: list[str], causes: dict[str, Exception], items: list[dict]):
        self.path = path
        self.metrics = metrics
        self.causes = causes
        self.items = items
        self.partial_result = None  # set by get_video_insights/get_post_insights before re-raising
        detail = "; ".join(f"{m}: {causes[m]}" for m in metrics)
        super().__init__(f"{len(metrics)} metric(s) failed for {path} — {detail}")


class PaginationError(RuntimeError):
    """Raised when a paginated Graph API fetch fails before exhausting all pages. Carries the
    pages that DID succeed (`items`) and, once the caller attaches it, the partially-populated
    result (`partial_result`) so failures never discard data for the pages that worked."""

    def __init__(self, path: str, params: dict | None, causes: list[dict], items: list[dict]):
        self.path = path
        self.params = params
        self.causes = causes
        self.items = items
        self.partial_result = None  # set by get_posts/get_videos before re-raising
        detail = "; ".join(str(c["exception"]) for c in causes)
        super().__init__(f"Pagination failed for {path} after {len(items)} result(s) — {detail}")


# ------------------------------------------------------------------
# API client
# ------------------------------------------------------------------


class FacebookClient:
    def __init__(self, access_token: str) -> None:
        self._session = requests.Session()
        self._session.params = {"access_token": access_token}  # type: ignore[assignment]

    def _get(self, path: str, params: dict | None = None, retries: int = 3) -> dict:
        url = f"{FB_GRAPH_BASE}/{path.lstrip('/')}"
        # 429 gets its own fixed 60s cooldown (Graph API rate-limit window), separate from the
        # exponential backoff used for transient 5xx errors and network failures below.
        retry_codes = [
            HTTPStatus.INTERNAL_SERVER_ERROR,
            HTTPStatus.BAD_GATEWAY,
            HTTPStatus.SERVICE_UNAVAILABLE,
            HTTPStatus.GATEWAY_TIMEOUT,
        ]

        last_exception: Exception | None = None
        for attempt in range(retries):
            wait = 2**attempt
            try:
                response = self._session.get(url, params=params or {}, timeout=30)
                response.raise_for_status()
                return response.json()
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exception = exc
                logger.warning("Request error for path %s, params %s, retrying in %ds: %s", path, params, wait, exc)
                time.sleep(wait)
            except HTTPError as exc:
                last_exception = exc
                code = exc.response.status_code
                if code == HTTPStatus.TOO_MANY_REQUESTS:
                    logger.warning("Rate limited for path %s, params %s, waiting 60s", path, params)
                    time.sleep(60)
                elif code in retry_codes:
                    logger.warning("HTTP %d for path %s, params %s, retrying in %ds", code, path, params, wait)
                    time.sleep(wait)
                else:
                    raise

        raise last_exception


    def _paginate(
        self, path: str, params: dict | None = None, max_results: int | None = None, raise_on_failure: bool = False
    ) -> list[dict]:
        '''

        Given a target path and params, will query the endpoint which returns an object with paging and data
        objects.
        page


        Parameters:
            path: Endpoint being queries
            params: fields being requests as part of http call to end point
            max_results: the maximum number of results that can be returned, by default is something odd like 3
                by passing None we ensure all results are returned



        Other:
            https://developers.facebook.com/docs/graph-api/results/
            Enpoint query returns an object with Data and Paging mappings
                Data json object:
                    The insights for the video/posts node/path, fields

                Paging json object:
                    Cursors object:
                        before : This is the cursor that points to the start of the page of data that has been
                        returnev - hash value.
                        after : This is the cursor that points to the end of the page of data that has been
                        returned - hash value.

                                    {
                    next : The Graph API endpoint that will return the next page of data. If not included,
                        this is the last page of data e.g.
                        "https://graph.facebook.com/{your-user-id}/albums?limit=25&before=NDMyNzQyODI3OTQw"
                    previous : The Graph API endpoint that will return the previous page of data. If not included,
                        this is the first page of data e.g.
                        "https://graph.facebook.com/{your-user-id}/albums?limit=25&before=NDMyNzQyODI3OTQw"


        Returns:
            results: A list of data objects, provided by all the endpoint calls for the given ids and params


        On failure of a single ID the logger raises a warning and
        '''
        results: list[dict] = []
        causes: list[dict] = []
        try:
            paging_json_object = self._get(path, params)  # Returns the json containing data and paging objects
            results.extend(paging_json_object.get("data", [])) # gets the data object for the first object
            logger.debug(f"Data retrieved from endpoint {path} with params {params}")
            # if we have a bad endpoint do we end the run completely?
        except Exception as e:
            logger.warning(f"Failed to return endpoint {path} with params {params}, error {e}")
            causes.append({'exception':e, 'path':path, 'params':params})
            if raise_on_failure:
                raise PaginationError(path, params, causes, results)
            return results

        while max_results is None or len(results) < max_results:
            paging = paging_json_object.get("paging", {})   # returns paging object, which contains the cursor
            after_cursor = paging.get("cursors", {}).get("after") # gets the next object hash, is it possible
            if not after_cursor or not paging.get("next"):
                break
            try:
                paging_json_object = self._get(path, {**(params or {}), "after": after_cursor})
            except Exception as e:
                logger.warning(
                                "Pagination failed for %s after %d result(s); stopping early: %s", path, len(results), e
                            )
                causes.append({'exception':e, 'path':path, 'params':params})
                if raise_on_failure:
                    raise PaginationError(path, params, causes, results)
                break
            results.extend(paging_json_object.get("data",[]))

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

    def _fetch_insight_items(
        self, path: str, metric_names: list[str], params: dict, raise_on_failure: bool = False
    ) -> list[dict]:
        """Fetches insight items, falling back to per-metric requests on a 400.

        The Graph API rejects the entire batch if any single metric is unavailable
        for the page (e.g. follower metrics require specific permissions). Retrying
        individually lets us collect what is accessible and skip what is not.

        If raise_on_failure is True, any metric(s) that still fail after the per-metric
        retry raise InsightMetricError (naming the specific metric and cause) instead of
        being silently skipped. The metrics that did succeed are still returned/attached.
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
        causes: dict[str, Exception] = {}
        for metric in metric_names:
            try:
                data = self._get(path, {**params, "metric": metric})
                items.extend(data.get("data", []))
            except requests.HTTPError as e:
                skipped.append(metric)
                causes[metric] = e
        if skipped:
            if raise_on_failure:
                logger.error("Metric(s) failed for %s: %s", path, ", ".join(skipped))
                raise InsightMetricError(path, skipped, causes, items)
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

    @staticmethod
    def _build_posts(raw_posts: list[dict], page_id: str) -> list[Post]:
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

    def get_posts(self, page_id: str, limit: int | None = None, max_results: int | None = None) -> list[Post]:
        params: dict = {
            "fields": f"id,message,created_time,permalink_url,attachments{{type}},{_REACTION_FIELDS}"
        }
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
