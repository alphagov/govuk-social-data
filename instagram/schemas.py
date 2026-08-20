import pyarrow as pa

from instagram.models import IGAccount, IGAccountInsightMetric, IGMedia

ACCOUNT_SCHEMA: dict[str, pa.DataType] = {
    "ig_user_id": pa.string(),
    "username": pa.string(),
    "name": pa.string(),
    "biography": pa.string(),
    "followers_count": pa.int64(),
    "follows_count": pa.int64(),
    "media_count": pa.int64(),
}

ACCOUNT_INSIGHTS_SCHEMA: dict[str, pa.DataType] = {
    "ig_user_id": pa.string(),
    "metric": pa.string(),
    "period": pa.string(),
    "end_time": pa.timestamp("s"),
    "value": pa.int64(),
}

MEDIA_SCHEMA: dict[str, pa.DataType] = {
    "ig_user_id": pa.string(),
    "media_id": pa.string(),
    "caption": pa.string(),
    "media_type": pa.string(),
    "media_product_type": pa.string(),
    "permalink": pa.string(),
    "timestamp": pa.timestamp("s"),
    "like_count": pa.int64(),
    "comments_count": pa.int64(),
}

MEDIA_INSIGHTS_SCHEMA: dict[str, pa.DataType] = {
    "media_id": pa.string(),
    "metric": pa.string(),
    "period": pa.string(),
    "value": pa.int64(),
}


def _cast_table(data: dict, schema: dict[str, pa.DataType]) -> pa.Table:
    table = pa.table(data)
    return table.cast(pa.schema([(k, schema[k]) for k in schema]))


def account_to_arrow(account: IGAccount, ig_user_id: str) -> pa.Table:
    return _cast_table(
        {
            "ig_user_id": [ig_user_id],
            "username": [account.username],
            "name": [account.name],
            "biography": [account.biography],
            "followers_count": [account.followers_count],
            "follows_count": [account.follows_count],
            "media_count": [account.media_count],
        },
        ACCOUNT_SCHEMA,
    )


def account_insights_to_arrow(ig_user_id: str, metrics: list[IGAccountInsightMetric]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in ACCOUNT_INSIGHTS_SCHEMA}
    for metric in metrics:
        for v in metric.values:
            rows["ig_user_id"].append(ig_user_id)
            rows["metric"].append(metric.name)
            rows["period"].append(metric.period)
            rows["end_time"].append(v.end_time)
            rows["value"].append(int(v.value))
    return _cast_table(rows, ACCOUNT_INSIGHTS_SCHEMA)


def media_to_arrow(ig_user_id: str, media: list[IGMedia]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in MEDIA_SCHEMA}
    for m in media:
        rows["ig_user_id"].append(ig_user_id)
        rows["media_id"].append(m.id)
        rows["caption"].append(m.caption)
        rows["media_type"].append(m.media_type)
        rows["media_product_type"].append(m.media_product_type)
        rows["permalink"].append(m.permalink)
        rows["timestamp"].append(m.timestamp)
        rows["like_count"].append(m.like_count)
        rows["comments_count"].append(m.comments_count)
    return _cast_table(rows, MEDIA_SCHEMA)


def media_insights_to_arrow(media: list[IGMedia]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in MEDIA_INSIGHTS_SCHEMA}
    for m in media:
        for insight in m.insights:
            if insight.value is None:
                continue
            rows["media_id"].append(m.id)
            rows["metric"].append(insight.name)
            rows["period"].append(insight.period)
            rows["value"].append(insight.value)
    return _cast_table(rows, MEDIA_INSIGHTS_SCHEMA)
