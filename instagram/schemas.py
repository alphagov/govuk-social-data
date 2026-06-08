from datetime import date, datetime

import pyarrow as pa

from instagram.models import IGAccount, IGAccountInsightMetric, IGMedia, IGMediaInsightMetric

ACCOUNT_SCHEMA: dict[str, pa.DataType] = {
    "ig_user_id": pa.string(),
    "username": pa.string(),
    "name": pa.string(),
    "biography": pa.string(),
    "followers_count": pa.int64(),
    "follows_count": pa.int64(),
    "media_count": pa.int64(),
    "target_date": pa.date32(),
    "ingestion_ts": pa.timestamp("us", tz="UTC"),
}

ACCOUNT_INSIGHTS_SCHEMA: dict[str, pa.DataType] = {
    "ig_user_id": pa.string(),
    "metric": pa.string(),
    "period": pa.string(),
    "end_time": pa.timestamp("s"),
    "value": pa.int64(),
    "target_date": pa.date32(),
    "ingestion_ts": pa.timestamp("us", tz="UTC"),
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
    "target_date": pa.date32(),
    "ingestion_ts": pa.timestamp("us", tz="UTC"),
}

MEDIA_INSIGHTS_SCHEMA: dict[str, pa.DataType] = {
    "media_id": pa.string(),
    "metric": pa.string(),
    "period": pa.string(),
    "value": pa.int64(),
    "target_date": pa.date32(),
    "ingestion_ts": pa.timestamp("us", tz="UTC"),
}


def _cast_table(data: dict, schema: dict[str, pa.DataType]) -> pa.Table:
    table = pa.table(data)
    return table.cast(pa.schema([(k, schema[k]) for k in schema]))


def account_to_arrow(account: IGAccount, ig_user_id: str, target_date: date, ingestion_ts: datetime) -> pa.Table:
    return _cast_table(
        {
            "ig_user_id": [ig_user_id],
            "username": [account.username],
            "name": [account.name],
            "biography": [account.biography],
            "followers_count": [account.followers_count],
            "follows_count": [account.follows_count],
            "media_count": [account.media_count],
            "target_date": [target_date],
            "ingestion_ts": [ingestion_ts],
        },
        ACCOUNT_SCHEMA,
    )


def account_insights_to_arrow(
    ig_user_id: str, metrics: list[IGAccountInsightMetric], target_date: date, ingestion_ts: datetime
) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in ACCOUNT_INSIGHTS_SCHEMA}
    for metric in metrics:
        for v in metric.values:
            rows["ig_user_id"].append(ig_user_id)
            rows["metric"].append(metric.name)
            rows["period"].append(metric.period)
            rows["end_time"].append(v.end_time)
            rows["value"].append(int(v.value))
            rows["target_date"].append(target_date)
            rows["ingestion_ts"].append(ingestion_ts)
    return _cast_table(rows, ACCOUNT_INSIGHTS_SCHEMA)


def media_to_arrow(ig_user_id: str, media: list[IGMedia], target_date: date, ingestion_ts: datetime) -> pa.Table:
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
        rows["target_date"].append(target_date)
        rows["ingestion_ts"].append(ingestion_ts)
    return _cast_table(rows, MEDIA_SCHEMA)


def media_insights_to_arrow(
    media_id: str, insights: list[IGMediaInsightMetric], target_date: date, ingestion_ts: datetime
) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in MEDIA_INSIGHTS_SCHEMA}
    for insight in insights:
        if insight.value is None:
            continue
        rows["media_id"].append(media_id)
        rows["metric"].append(insight.name)
        rows["period"].append(insight.period)
        rows["value"].append(insight.value)
        rows["target_date"].append(target_date)
        rows["ingestion_ts"].append(ingestion_ts)
    return _cast_table(rows, MEDIA_INSIGHTS_SCHEMA)
