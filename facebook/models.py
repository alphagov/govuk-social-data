import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)


class PageInsightValue(BaseModel):
    end_time: datetime | None = None  # present on day-period metrics; absent on lifetime metrics
    value: int


class PageInsightMetric(BaseModel):
    name: str
    period: str
    values: list[PageInsightValue]


class PostInsightMetric(BaseModel):
    name: str
    period: str
    value: int | dict


class FacebookReelInsights(BaseModel):
    """Typed representation of the /{video_id}/video_insights response for Reel content.

    All fields are Optional — the API may omit metrics that have no data yet, and new
    metrics may appear as Facebook evolves the API. Unknown metrics are logged as warnings
    so the schema can be updated without silent data loss.

    Ref: https://developers.facebook.com/docs/graph-api/reference/video/video_insights/#metrics
    """

    model_config = ConfigDict(extra="ignore")

    # Scalar metrics
    post_video_avg_time_watched: int | None = Field(
        default=None, description="Avg time (ms) reel was played per view."
    )
    post_video_view_time: int | None = Field(
        default=None, description="Total time (ms) reel was played, including replays."
    )
    post_impressions_unique: int | None = Field(
        default=None, description="Unique reach — people who saw reel at least once."
    )
    blue_reels_play_count: int | None = Field(default=None, description="Plays after impression; excludes replays.")
    fb_reels_total_plays: int | None = Field(default=None, description="Total plays including replays.")
    fb_reels_replay_count: int | None = Field(default=None, description="Replays only.")
    post_video_followers: int | None = Field(default=None, description="Page follows within 24h of watching.")

    # Dict metrics
    post_video_likes_by_reaction_type: dict[str, int] | None = Field(
        default=None, description="Reaction type → count (e.g. REACTION_LIKE)."
    )
    post_video_social_actions: dict[str, int] | None = Field(
        default=None, description="Social action type → count (SHARE, COMMENT)."
    )
    post_video_retention_graph: dict[str, float] | None = Field(
        default=None, description="Second (str key) → retention percentage (0–1)."
    )

    @model_validator(mode="before")
    @classmethod
    def flatten_facebook_payload(cls, data: Any) -> Any:
        """Flattens the raw Graph API response into a flat field dict.

        Accepts either:
          - {"data": [{"name": "metric_name", "values": [{"value": ...}]}, ...]}
          - An already-flat dict (used in tests and model_copy scenarios)
        """
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            known = set(cls.model_fields.keys())
            flat: dict = {}
            for item in data["data"]:
                name = item.get("name")
                values_list = item.get("values", [])
                if not name or not isinstance(values_list, list) or not values_list:
                    continue
                flat[name] = values_list[0].get("value")
                if name not in known:
                    logger.warning("Unexpected reel metric from API (schema may need updating): %r", name)
            return flat
        return data


class Video(BaseModel):
    video_id: str
    page_id: str
    title: str | None = None
    description: str | None = None
    created_time: datetime
    length: float | None = None  # duration in seconds
    insights: FacebookReelInsights | None = None


class Post(BaseModel):
    post_id: str
    page_id: str
    message: str | None = None
    created_time: datetime
    post_type: str
    permalink_url: str | None = None
    insights: list[PostInsightMetric] = []


class Page(BaseModel):
    page_id: str
    name: str
    fan_count: int | None = None
    followers_count: int | None = None
    talking_about_count: int | None = None
    insights: list[PageInsightMetric] = []
    posts: list[Post] = []
