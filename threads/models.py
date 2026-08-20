"""Pydantic containers for the data coming back from the Threads Graph API.

Ref: https://developers.facebook.com/docs/threads/threads-media
"""

from datetime import datetime

from pydantic import BaseModel


class ThreadsProfile(BaseModel):
    id: str
    username: str | None = None
    name: str | None = None
    threads_profile_picture_url: str | None = None
    threads_biography: str | None = None
    is_verified: bool | None = None


class ThreadsInsightValue(BaseModel):
    end_time: datetime | None = None  # present on time-windowed metrics; absent on lifetime ones
    value: int | float


class ThreadsAccountInsightMetric(BaseModel):
    # period is optional because the total_value and lifetime response shapes do not always carry one.
    name: str
    period: str | None = None
    values: list[ThreadsInsightValue] = []


class ThreadsMediaInsightMetric(BaseModel):
    name: str
    period: str | None = None
    value: int | None = None


class ThreadsLinkClick(BaseModel):
    """One entry of the `clicks` metric's link_total_values array."""

    link_url: str | None = None
    value: int | None = None


class ThreadsDemographicRow(BaseModel):
    """One follower_demographics bucket, flattened out of the nested breakdown response."""

    breakdown: str  # "age" | "gender" | "country" | "city"
    dimension_value: str  # "25-34", "F", "GB", "London"
    value: int | None = None


class ThreadsMedia(BaseModel):
    id: str
    media_product_type: str | None = None
    media_type: str | None = None
    permalink: str | None = None
    text: str | None = None
    timestamp: datetime | None = None
    shortcode: str | None = None
    is_quote_post: bool | None = None
    has_replies: bool | None = None
    link_attachment_url: str | None = None
    gif_url: str | None = None
    alt_text: str | None = None
    poll_attachment: dict | None = None
    # The docs describe a tag object while community reports show a bare string; the union costs
    # nothing and the converter JSON-encodes either shape.
    topic_tag: dict | str | None = None
    insights: list[ThreadsMediaInsightMetric] = []


class ThreadsReply(BaseModel):
    id: str
    media_type: str | None = None
    text: str | None = None
    timestamp: datetime | None = None
    shortcode: str | None = None
    permalink: str | None = None
    has_replies: bool | None = None
    is_reply: bool | None = None
    root_post: dict | None = None  # {"id": "..."} — the converter lifts .id out
    replied_to: dict | None = None  # same
    hide_status: str | None = None
