from datetime import datetime

from pydantic import BaseModel


class PageInsightValue(BaseModel):
    end_time: datetime
    value: int


class PageInsightMetric(BaseModel):
    name: str
    period: str
    values: list[PageInsightValue]


class PostReactions(BaseModel):
    like: int = 0
    love: int = 0
    wow: int = 0
    haha: int = 0
    sorry: int = 0
    anger: int = 0


class PostInsightMetric(BaseModel):
    name: str
    period: str
    value: int | dict


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
