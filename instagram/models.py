from datetime import datetime

from pydantic import BaseModel


class IGAccount(BaseModel):
    id: str
    username: str
    name: str | None = None
    biography: str | None = None
    followers_count: int | None = None
    follows_count: int | None = None
    media_count: int | None = None


class IGInsightValue(BaseModel):
    end_time: datetime | None = None
    value: int | float


class IGAccountInsightMetric(BaseModel):
    name: str
    period: str
    values: list[IGInsightValue] = []


class IGMedia(BaseModel):
    id: str
    caption: str | None = None
    media_type: str | None = None
    media_product_type: str | None = None
    permalink: str | None = None
    thumbnail_url: str | None = None
    media_url: str | None = None
    timestamp: datetime | None = None
    like_count: int | None = None
    comments_count: int | None = None


class IGMediaInsightMetric(BaseModel):
    name: str
    period: str
    value: int | None = None
