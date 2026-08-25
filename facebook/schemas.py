import pyarrow as pa

from facebook.api import VIDEO_SCALAR_METRICS
from facebook.models import Page, PageInsightMetric, Post, Video

# This file contains the schema objects for the pyarrow upload to bq

PAGE_INFO_SCHEMA: dict[str, pa.DataType] = {
    "page_id": pa.string(),
    "name": pa.string(),
    "fan_count": pa.int64(),
    "followers_count": pa.int64(),
    "talking_about_count": pa.int64(),
}

PAGE_INSIGHTS_SCHEMA: dict[str, pa.DataType] = {
    "page_id": pa.string(),
    "metric": pa.string(),
    "period": pa.string(),
    "end_time": pa.timestamp("s"),
    "value": pa.int64(),
}

POSTS_SCHEMA: dict[str, pa.DataType] = {
    "post_id": pa.string(),
    "page_id": pa.string(),
    "message": pa.string(),
    "created_time": pa.timestamp("s"),
    "post_type": pa.string(),
    "permalink_url": pa.string(),
}

POST_INSIGHTS_SCHEMA: dict[str, pa.DataType] = {
    "post_id": pa.string(),
    "metric": pa.string(),
    "period": pa.string(),
    "value": pa.int64(),
}

POST_REACTIONS_SCHEMA: dict[str, pa.DataType] = {
    "post_id": pa.string(),
    "like": pa.int64(),
    "love": pa.int64(),
    "wow": pa.int64(),
    "haha": pa.int64(),
    "sad": pa.int64(),
    "angry": pa.int64(),
}

# post_activity_by_action_type_unique: {action_type: unique_users} — keys are variable
POST_ACTIVITY_SCHEMA: dict[str, pa.DataType] = {
    "post_id": pa.string(),
    "action_type": pa.string(),
    "unique_users": pa.int64(),
}

# post_clicks_by_type: {click_type: count} — keys are variable
POST_CLICKS_BY_TYPE_SCHEMA: dict[str, pa.DataType] = {
    "post_id": pa.string(),
    "click_type": pa.string(),
    "clicks": pa.int64(),
}

VIDEOS_SCHEMA: dict[str, pa.DataType] = {
    "video_id": pa.string(),
    "page_id": pa.string(),
    "associated_post_id": pa.string(),
    "title": pa.string(),
    "description": pa.string(),
    "created_time": pa.timestamp("s"),
    "length": pa.float64(),
}

# Scalar Reel metrics — one row per (video_id, metric)
VIDEO_INSIGHTS_SCHEMA: dict[str, pa.DataType] = {
    "video_id": pa.string(),
    "metric": pa.string(),
    "period": pa.string(),
    "value": pa.int64(),
}

# post_video_likes_by_reaction_type: {REACTION_*: count}
VIDEO_REEL_REACTIONS_SCHEMA: dict[str, pa.DataType] = {
    "video_id": pa.string(),
    "reaction_type": pa.string(),
    "count": pa.int64(),
}

# post_video_social_actions: {SHARE: n, COMMENT: n}
VIDEO_REEL_SOCIAL_ACTIONS_SCHEMA: dict[str, pa.DataType] = {
    "video_id": pa.string(),
    "action_type": pa.string(),
    "count": pa.int64(),
}

# post_video_retention_graph: {second_str: retention_pct}
VIDEO_REEL_RETENTION_SCHEMA: dict[str, pa.DataType] = {
    "video_id": pa.string(),
    "second": pa.string(),
    "retention_pct": pa.float64(),
}


def _cast_table(data: dict, schema: dict[str, pa.DataType]) -> pa.Table:
    table = pa.table(data)
    return table.cast(pa.schema([(k, schema[k]) for k in schema]))


def page_info_to_arrow(page: Page) -> pa.Table:
    return _cast_table(
        {
            "page_id": [page.page_id],
            "name": [page.name],
            "fan_count": [page.fan_count],
            "followers_count": [page.followers_count],
            "talking_about_count": [page.talking_about_count],
        },
        PAGE_INFO_SCHEMA,
    )


def page_insights_to_arrow(page_id: str, metrics: list[PageInsightMetric]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in PAGE_INSIGHTS_SCHEMA}
    for metric in metrics:
        for v in metric.values:
            rows["page_id"].append(page_id)
            rows["metric"].append(metric.name)
            rows["period"].append(metric.period)
            rows["end_time"].append(v.end_time)
            rows["value"].append(v.value)
    return _cast_table(rows, PAGE_INSIGHTS_SCHEMA)


def posts_to_arrow(posts: list[Post]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in POSTS_SCHEMA}
    for post in posts:
        rows["post_id"].append(post.post_id)
        rows["page_id"].append(post.page_id)
        rows["message"].append(post.message)
        rows["created_time"].append(post.created_time)
        rows["post_type"].append(post.post_type)
        rows["permalink_url"].append(post.permalink_url)
    return _cast_table(rows, POSTS_SCHEMA)


def post_insights_to_arrow(posts: list[Post]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in POST_INSIGHTS_SCHEMA}
    for post in posts:
        for insight in post.insights:
            if isinstance(insight.value, dict):
                continue
            rows["post_id"].append(post.post_id)
            rows["metric"].append(insight.name)
            rows["period"].append(insight.period)
            rows["value"].append(insight.value)
    return _cast_table(rows, POST_INSIGHTS_SCHEMA)


# toadd
def videos_to_arrow(videos: list[Video]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in VIDEOS_SCHEMA}
    for video in videos:
        rows["video_id"].append(video.video_id)
        rows["page_id"].append(video.page_id)
        rows["associated_post_id"].append(video.associated_post_id)
        rows["title"].append(video.title)
        rows["description"].append(video.description)
        rows["created_time"].append(video.created_time)
        rows["length"].append(video.length)
    return _cast_table(rows, VIDEOS_SCHEMA)


def video_insights_to_arrow(videos: list[Video]) -> pa.Table:
    """Scalar Reel metrics only — one row per (video_id, metric).
    Dict metrics (reactions, social actions, retention) have their own converters.
    """
    rows: dict[str, list] = {k: [] for k in VIDEO_INSIGHTS_SCHEMA}
    for video in videos:
        if video.insights is None:
            continue
        for field in VIDEO_SCALAR_METRICS:
            value = getattr(video.insights, field)
            if value is None:
                continue
            rows["video_id"].append(video.video_id)
            rows["metric"].append(field)
            rows["period"].append("lifetime")
            rows["value"].append(value)
    return _cast_table(rows, VIDEO_INSIGHTS_SCHEMA)


def video_reel_reactions_to_arrow(videos: list[Video]) -> pa.Table:
    """Flattens post_video_likes_by_reaction_type into (video_id, reaction_type, count) rows."""
    rows: dict[str, list] = {k: [] for k in VIDEO_REEL_REACTIONS_SCHEMA}
    for video in videos:
        if video.insights is None or video.insights.post_video_likes_by_reaction_type is None:
            continue
        for reaction_type, count in video.insights.post_video_likes_by_reaction_type.items():
            rows["video_id"].append(video.video_id)
            rows["reaction_type"].append(reaction_type)
            rows["count"].append(count)
    return _cast_table(rows, VIDEO_REEL_REACTIONS_SCHEMA)


def video_reel_social_actions_to_arrow(videos: list[Video]) -> pa.Table:
    """Flattens post_video_social_actions into (video_id, action_type, count) rows."""
    rows: dict[str, list] = {k: [] for k in VIDEO_REEL_SOCIAL_ACTIONS_SCHEMA}
    for video in videos:
        if video.insights is None or video.insights.post_video_social_actions is None:
            continue
        for action_type, count in video.insights.post_video_social_actions.items():
            rows["video_id"].append(video.video_id)
            rows["action_type"].append(action_type)
            rows["count"].append(count)
    return _cast_table(rows, VIDEO_REEL_SOCIAL_ACTIONS_SCHEMA)


def video_reel_retention_to_arrow(videos: list[Video]) -> pa.Table:
    """Flattens post_video_retention_graph into (video_id, second, retention_pct) rows."""
    rows: dict[str, list] = {k: [] for k in VIDEO_REEL_RETENTION_SCHEMA}
    for video in videos:
        if video.insights is None or video.insights.post_video_retention_graph is None:
            continue
        for second, retention_pct in video.insights.post_video_retention_graph.items():
            rows["video_id"].append(video.video_id)
            rows["second"].append(second)
            rows["retention_pct"].append(retention_pct)
    return _cast_table(rows, VIDEO_REEL_RETENTION_SCHEMA)


def post_reactions_to_arrow(posts: list[Post]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in POST_REACTIONS_SCHEMA}
    for post in posts:
        if not post.reactions:
            continue
        rows["post_id"].append(post.post_id)
        rows["like"].append(post.reactions.get("LIKE", 0))
        rows["love"].append(post.reactions.get("LOVE", 0))
        rows["wow"].append(post.reactions.get("WOW", 0))
        rows["haha"].append(post.reactions.get("HAHA", 0))
        rows["sad"].append(post.reactions.get("SAD", 0))
        rows["angry"].append(post.reactions.get("ANGRY", 0))
    return _cast_table(rows, POST_REACTIONS_SCHEMA)


def post_activity_to_arrow(posts: list[Post]) -> pa.Table:
    """Flattens post_activity_by_action_type_unique into (post_id, action_type, unique_users) rows."""
    rows: dict[str, list] = {k: [] for k in POST_ACTIVITY_SCHEMA}
    for post in posts:
        activity_insight = next(
            (i for i in post.insights if i.name == "post_activity_by_action_type_unique" and i.period == "lifetime"),
            None,
        )
        if activity_insight is None or not isinstance(activity_insight.value, dict):
            continue
        for action_type, unique_users in activity_insight.value.items():
            rows["post_id"].append(post.post_id)
            rows["action_type"].append(action_type)
            rows["unique_users"].append(unique_users)
    return _cast_table(rows, POST_ACTIVITY_SCHEMA)


def post_clicks_by_type_to_arrow(posts: list[Post]) -> pa.Table:
    """Flattens post_clicks_by_type into (post_id, click_type, clicks) rows."""
    rows: dict[str, list] = {k: [] for k in POST_CLICKS_BY_TYPE_SCHEMA}
    for post in posts:
        clicks_insight = next(
            (i for i in post.insights if i.name == "post_clicks_by_type" and i.period == "lifetime"),
            None,
        )
        if clicks_insight is None or not isinstance(clicks_insight.value, dict):
            continue
        for click_type, count in clicks_insight.value.items():
            rows["post_id"].append(post.post_id)
            rows["click_type"].append(click_type)
            rows["clicks"].append(count)
    return _cast_table(rows, POST_CLICKS_BY_TYPE_SCHEMA)
