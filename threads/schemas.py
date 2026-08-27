"""Output schemas for the PyArrow tables uploaded to BigQuery, plus the converters that build them.

Each schema is a dict mapping column name to PyArrow type — deliberately a dict rather than a
pa.schema, because upload_partitioned extends it with target_date and ingestion_timestamp via
with_ingestion_cols before the load. Those two columns are therefore not listed here.

"""

import json

import pyarrow as pa

from threads.models import (
    ThreadsAccountInsightMetric,
    ThreadsDemographicRow,
    ThreadsMedia,
    ThreadsProfile,
    ThreadsReply,
)

# --- Snapshot-mode tables ---------------------------------------------------------------------

PROFILE_SCHEMA: dict[str, pa.DataType] = {
    "threads_user_id": pa.string(),
    "username": pa.string(),
    "name": pa.string(),
    "biography": pa.string(),
    "profile_picture_url": pa.string(),
    "is_verified": pa.bool_(),  # first boolean column in this repo; Threads is the only source of flags
}

MEDIA_SCHEMA: dict[str, pa.DataType] = {
    "threads_user_id": pa.string(),
    "media_id": pa.string(),
    "media_type": pa.string(),
    "media_product_type": pa.string(),
    "permalink": pa.string(),
    "text": pa.string(),
    "timestamp": pa.timestamp("s"),
    "shortcode": pa.string(),
    "is_quote_post": pa.bool_(),
    "has_replies": pa.bool_(),
    "link_attachment_url": pa.string(),
    "gif_url": pa.string(),
    "alt_text": pa.string(),
    # poll_attachment and topic_tag are the only nested values Threads returns. They are stored as
    # raw JSON strings so this stage stays transformation-free — downstream reads them with
    # JSON_VALUE rather than this pipeline picking which sub-fields matter.
    "poll_attachment": pa.string(),
    "topic_tag": pa.string(),
}

MEDIA_INSIGHTS_SCHEMA: dict[str, pa.DataType] = {
    "media_id": pa.string(),
    "metric": pa.string(),
    "period": pa.string(),
    "value": pa.int64(),
}

REPLIES_SCHEMA: dict[str, pa.DataType] = {
    "threads_user_id": pa.string(),
    "reply_id": pa.string(),
    "media_type": pa.string(),
    "text": pa.string(),
    "timestamp": pa.timestamp("s"),
    "shortcode": pa.string(),
    "permalink": pa.string(),
    "has_replies": pa.bool_(),
    "is_reply": pa.bool_(),
    "root_post_id": pa.string(),
    "replied_to_id": pa.string(),
    "hide_status": pa.string(),
}

# followers_count and anything else with no since/until support — "what is true right now".
ACCOUNT_LIFETIME_SCHEMA: dict[str, pa.DataType] = {
    "threads_user_id": pa.string(),
    "metric": pa.string(),
    "value": pa.int64(),
}

FOLLOWER_DEMOGRAPHICS_SCHEMA: dict[str, pa.DataType] = {
    "threads_user_id": pa.string(),
    "breakdown": pa.string(),
    "dimension_value": pa.string(),
    "value": pa.int64(),
}

# --- Daily-mode table ---------------------------------------------------------------------------

ACCOUNT_INSIGHTS_SCHEMA: dict[str, pa.DataType] = {
    "threads_user_id": pa.string(),
    "metric": pa.string(),
    "period": pa.string(),
    "end_time": pa.timestamp("s"),
    "value": pa.int64(),
}


def _cast_table(data: dict, schema: dict[str, pa.DataType]) -> pa.Table:
    table = pa.table(data)
    return table.cast(pa.schema([(k, schema[k]) for k in schema]))


def _json_column(value: dict | str | None) -> str | None:
    """Render a nested API value as the raw JSON string stored in a pa.string() column.

    A string is passed through untouched — topic_tag may arrive as either a tag object or a bare
    tag name, and double-encoding the latter would bury it inside quotes.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def profile_to_arrow(profile: ThreadsProfile, threads_user_id: str) -> pa.Table:
    return _cast_table(
        {
            "threads_user_id": [threads_user_id],
            "username": [profile.username],
            "name": [profile.name],
            "biography": [profile.threads_biography],
            "profile_picture_url": [profile.threads_profile_picture_url],
            "is_verified": [profile.is_verified],
        },
        PROFILE_SCHEMA,
    )


def media_to_arrow(threads_user_id: str, media: list[ThreadsMedia]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in MEDIA_SCHEMA}
    for m in media:
        rows["threads_user_id"].append(threads_user_id)
        rows["media_id"].append(m.id)
        rows["media_type"].append(m.media_type)
        rows["media_product_type"].append(m.media_product_type)
        rows["permalink"].append(m.permalink)
        rows["text"].append(m.text)
        rows["timestamp"].append(m.timestamp)
        rows["shortcode"].append(m.shortcode)
        rows["is_quote_post"].append(m.is_quote_post)
        rows["has_replies"].append(m.has_replies)
        rows["link_attachment_url"].append(m.link_attachment_url)
        rows["gif_url"].append(m.gif_url)
        rows["alt_text"].append(m.alt_text)
        rows["poll_attachment"].append(_json_column(m.poll_attachment))
        rows["topic_tag"].append(_json_column(m.topic_tag))
    return _cast_table(rows, MEDIA_SCHEMA)


def media_insights_to_arrow(media: list[ThreadsMedia]) -> pa.Table:
    """One row per (media, metric). media_id joins back to threads_media, so no user id is carried."""
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


def replies_to_arrow(threads_user_id: str, replies: list[ThreadsReply]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in REPLIES_SCHEMA}
    for r in replies:
        rows["threads_user_id"].append(threads_user_id)
        rows["reply_id"].append(r.id)
        rows["media_type"].append(r.media_type)
        rows["text"].append(r.text)
        rows["timestamp"].append(r.timestamp)
        rows["shortcode"].append(r.shortcode)
        rows["permalink"].append(r.permalink)
        rows["has_replies"].append(r.has_replies)
        rows["is_reply"].append(r.is_reply)
        # A reply that is itself a root post carries neither object; NULL rather than a KeyError.
        rows["root_post_id"].append((r.root_post or {}).get("id"))
        rows["replied_to_id"].append((r.replied_to or {}).get("id"))
        rows["hide_status"].append(r.hide_status)
    return _cast_table(rows, REPLIES_SCHEMA)


def account_lifetime_to_arrow(threads_user_id: str, metrics: list[ThreadsAccountInsightMetric]) -> pa.Table:
    """One row per lifetime metric.

    Takes the same model as account_insights_to_arrow but drops end_time and period, because a
    lifetime metric returns a single total_value carrying neither. threads/api.py normalises that
    shape into a one-entry values list, which is why this can stay a plain loop.
    """
    rows: dict[str, list] = {k: [] for k in ACCOUNT_LIFETIME_SCHEMA}
    for metric in metrics:
        for v in metric.values:
            rows["threads_user_id"].append(threads_user_id)
            rows["metric"].append(metric.name)
            rows["value"].append(int(v.value))
    return _cast_table(rows, ACCOUNT_LIFETIME_SCHEMA)


def follower_demographics_to_arrow(threads_user_id: str, demographics: list[ThreadsDemographicRow]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in FOLLOWER_DEMOGRAPHICS_SCHEMA}
    for row in demographics:
        if row.value is None:
            continue
        rows["threads_user_id"].append(threads_user_id)
        rows["breakdown"].append(row.breakdown)
        rows["dimension_value"].append(row.dimension_value)
        rows["value"].append(int(row.value))
    return _cast_table(rows, FOLLOWER_DEMOGRAPHICS_SCHEMA)


def account_insights_to_arrow(threads_user_id: str, metrics: list[ThreadsAccountInsightMetric]) -> pa.Table:
    rows: dict[str, list] = {k: [] for k in ACCOUNT_INSIGHTS_SCHEMA}
    for metric in metrics:
        for v in metric.values:
            rows["threads_user_id"].append(threads_user_id)
            rows["metric"].append(metric.name)
            rows["period"].append(metric.period)
            rows["end_time"].append(v.end_time)
            rows["value"].append(int(v.value))
    return _cast_table(rows, ACCOUNT_INSIGHTS_SCHEMA)
