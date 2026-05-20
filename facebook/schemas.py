import pyarrow as pa

from facebook.models import Page, PageInsightMetric, Post, PostInsightMetric, PostReactions

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
    "sorry": pa.int64(),
    "anger": pa.int64(),
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


def post_reactions_to_arrow(posts: list[Post]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in POST_REACTIONS_SCHEMA}
    for post in posts:
        reactions_insight = next(
            (i for i in post.insights if i.name == "post_reactions_by_type_total"),
            None,
        )
        if reactions_insight is None or not isinstance(reactions_insight.value, dict):
            continue
        raw: dict = reactions_insight.value
        reactions = PostReactions(
            like=raw.get("LIKE", 0),
            love=raw.get("LOVE", 0),
            wow=raw.get("WOW", 0),
            haha=raw.get("HAHA", 0),
            sorry=raw.get("SORRY", 0),
            anger=raw.get("ANGER", 0),
        )
        rows["post_id"].append(post.post_id)
        rows["like"].append(reactions.like)
        rows["love"].append(reactions.love)
        rows["wow"].append(reactions.wow)
        rows["haha"].append(reactions.haha)
        rows["sorry"].append(reactions.sorry)
        rows["anger"].append(reactions.anger)
    return _cast_table(rows, POST_REACTIONS_SCHEMA)
