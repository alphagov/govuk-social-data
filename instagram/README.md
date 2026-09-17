# Instagram module

Reads analytics data from the Instagram Graph API and converts it to PyArrow tables. Mirrors the structure of `facebook/`.

## Architecture

```
instagram/
├── api.py       # InstagramClient + build_client() + metric constants
├── models.py    # Pydantic models (IGAccount, IGMedia, IGAccountInsightMetric, etc.)
├── schemas.py   # PyArrow schemas + table converters
└── main.py      # CLI entry point
```

## Authentication

Credentials are read from GCP Secret Manager (project `gds-social-data`):

| Secret name | Description |
|---|---|
| `instagram_bearer_token` | Long-lived Instagram Business token (plain string, not JSON-wrapped) |
| `instagram_user_id` | Instagram Business User ID |

Unlike the Facebook token, the Instagram token is stored as a raw string. `build_client()` calls `.strip()` before use to remove any trailing whitespace from Secret Manager.

Example cli to generate new tokens 
```bash
# 1) Exchange short lived for long lived (skipped step one and url 

redirect does not work with webapps)
instagram_app_secret=$(gcloud secrets versions access latest --secret="instagram_app_secret")
instagram_app_id=$(gcloud secrets versions access latest --secret="instagram_app_id")   

curl -G "https://graph.facebook.com/v24.0/oauth/access_token" \
  -d "grant_type=fb_exchange_token" \
  -d "client_id=${instagram_app_id}" \
  -d "client_secret=${instagram_app_secret}" \
  -d "fb_exchange_token=${SHORT_LIVED_USER_TOKEN}"

# 2) Exchange long lived for page
curl -G "https://graph.facebook.com/v24.0/me/accounts" \
  -d "fields=id,name,access_token,category,tasks" \
  -d "access_token=${LONG_LIVED_USER_TOKEN}"
```
## API base URL

```
https://graph.facebook.com/v25.0
```

Instagram Business accounts use the same Graph API host as Facebook Pages. The `/{ig_user_id}` node is an IG User node.

## Current metric constants

### Account insights — `ACCOUNT_DAY_METRICS`

Fetched via `GET /{ig_user_id}/insights?period=day` (time-series, one row per day).

| Metric | Notes |
|---|---|
| `reach` | Unique accounts that saw content. Estimated. |
| `follower_count` | **Only supports the last 30 days**; do not query dates older than 30 days. |

### Account insights — `ACCOUNT_TOTAL_VALUE_METRICS`

Fetched via `GET /{ig_user_id}/insights?metric_type=total_value&period=day`.

| Metric | Notes |
|---|---|
| `views` | Times content was played or displayed. In development. |

### Media insights — `MEDIA_LIFETIME_METRICS`

Fetched via `GET /{media_id}/insights?period=lifetime`.

| Metric | Notes |
|---|---|
| `reach` | Unique accounts that saw the media. |
| `views` | Times the media was played or displayed. |
| `likes` | Like count. **Not valid for STORY media.** |
| `comments` | Comment count. **Not valid for STORY media.** |
| `saved` | Save count. **Not valid for STORY media.** |
| `shares` | Share count. |
| `total_interactions` | Sum of all engagement metrics. |

`likes`, `comments`, and `saved` are not valid for STORY media. The client swallows HTTP errors per-media-item (`get_media_insights` returns the media item un-enriched, i.e. `insights=[]`, on failure) so a single story item in the feed does not break the pipeline.

## BigQuery output tables

Tables are written to dataset `govuk_instagram` in project `gds-social-data`. Each table is partitioned by `target_date` (the pipeline run date). All tables include `target_date` (DATE) and `ingestion_timestamp` (TIMESTAMP) columns appended by the upload helper.

### `instagram_account`

Full BQ ID: `gds-social-data.govuk_instagram.instagram_account`

One row per pipeline run — a snapshot of account state at `target_date`.

| Column | Type | Notes |
|---|---|---|
| `ig_user_id` | STRING | Instagram Business User ID |
| `username` | STRING | |
| `name` | STRING | Display name |
| `biography` | STRING | |
| `followers_count` | INT64 | |
| `follows_count` | INT64 | Accounts this account follows |
| `media_count` | INT64 | Total media items on the account |
| `target_date` | DATE | Partition key — pipeline run date |
| `ingestion_timestamp` | TIMESTAMP | UTC time of pipeline run |

### `instagram_account_insights`

Full BQ ID: `gds-social-data.govuk_instagram.instagram_account_insights`

Long format — one row per metric per day. Join to `instagram_account` on `ig_user_id`.

| Column | Type | Notes |
|---|---|---|
| `ig_user_id` | STRING | |
| `metric` | STRING | `reach`, `follower_count`, `views` |
| `period` | STRING | Always `"day"` |
| `end_time` | TIMESTAMP | End of the time window the value covers |
| `value` | INT64 | |
| `target_date` | DATE | Partition key |
| `ingestion_timestamp` | TIMESTAMP | |

### `instagram_media`

Full BQ ID: `gds-social-data.govuk_instagram.instagram_media`

One row per media item (all posts/reels/videos fetched at run time). `target_date` is the run date — use `timestamp` for the publish date. Each daily run overwrites its partition.

| Column | Type | Notes |
|---|---|---|
| `ig_user_id` | STRING | |
| `media_id` | STRING | Unique identifier for the post/reel/video |
| `caption` | STRING | |
| `media_type` | STRING | `IMAGE`, `VIDEO`, `CAROUSEL_ALBUM` |
| `media_product_type` | STRING | `FEED`, `STORY`, `REELS` |
| `permalink` | STRING | Public URL |
| `timestamp` | TIMESTAMP | When the media was published |
| `like_count` | INT64 | At time of ingestion |
| `comments_count` | INT64 | At time of ingestion |
| `target_date` | DATE | Partition key |
| `ingestion_timestamp` | TIMESTAMP | |

### `instagram_media_insights`

Full BQ ID: `gds-social-data.govuk_instagram.instagram_media_insights`

Long format — one row per metric per media item. All values are lifetime cumulative at time of ingestion. Join to `instagram_media` on `media_id`. `likes`, `comments`, and `saved` are absent for STORY media.

| Column | Type | Notes |
|---|---|---|
| `media_id` | STRING | |
| `metric` | STRING | `reach`, `views`, `likes`, `comments`, `saved`, `shares`, `total_interactions` |
| `period` | STRING | Always `"lifetime"` |
| `value` | INT64 | Cumulative lifetime value at time of ingestion |
| `target_date` | DATE | Partition key |
| `ingestion_timestamp` | TIMESTAMP | |

## Useful queries

### Account insights trend (pivoted)

```sql
SELECT
  target_date,
  MAX(IF(metric = 'reach',          value, NULL)) AS reach,
  MAX(IF(metric = 'follower_count', value, NULL)) AS follower_count,
  MAX(IF(metric = 'views',          value, NULL)) AS views
FROM `gds-social-data.govuk_instagram.instagram_account_insights`
GROUP BY target_date
ORDER BY target_date DESC
```

### Latest media snapshot with lifetime insights (pivoted)

```sql
SELECT
  m.media_id,
  m.media_type,
  m.media_product_type,
  m.caption,
  m.timestamp            AS published_at,
  m.like_count,
  m.comments_count,
  MAX(IF(mi.metric = 'reach',              mi.value, NULL)) AS reach,
  MAX(IF(mi.metric = 'views',              mi.value, NULL)) AS views,
  MAX(IF(mi.metric = 'saved',              mi.value, NULL)) AS saved,
  MAX(IF(mi.metric = 'shares',             mi.value, NULL)) AS shares,
  MAX(IF(mi.metric = 'total_interactions', mi.value, NULL)) AS total_interactions
FROM `gds-social-data.govuk_instagram.instagram_media` m
LEFT JOIN `gds-social-data.govuk_instagram.instagram_media_insights` mi
  USING (media_id)
WHERE m.target_date = (
  SELECT MAX(target_date) FROM `gds-social-data.govuk_instagram.instagram_media`
)
GROUP BY 1, 2, 3, 4, 5, 6, 7
ORDER BY m.timestamp DESC
```

## Deprecated metrics (do not add these)

As of April 21, 2025, the following are sunset across all API versions:

- `impressions` — replaced by `views` / `reach`
- `plays`, `clips_replays_count`, `ig_reels_aggregated_all_plays_count` — Reels-only, removed
- `video_views` — removed

Previously valid account metrics now removed (Oct 2024):
- `email_contacts`, `get_direction_clicks`, `profile_views`, `text_message_clicks`, `website_clicks`, `phone_call_clicks`

**`follower_count` is still valid** but restricted to a 30-day window. The eventual replacement is `follows_and_unfollows` (`metric_type=total_value`), which is not yet added to the module.

## Running locally

```bash
# Yesterday's data, all media
python -m instagram.main

# Custom date range, limit media items
python -m instagram.main --target-date 2026-06-08 --media-limit 10 --dataset govuk_instagram_dev

# Skip per-media insights
python -m instagram.main --no-run-media-insights

```

Note: `follower_count` will fail with HTTP 400 if `--target-date` is more than 30 days ago. Use `--target-date` within the last 30 days when account insights are needed.

## Running tests

```bash
# HTTP endpoint validation (live API, no Python client)
poetry run pytest tests/test_instagram_http.py -m integration -v

# Unit tests (no credentials needed)
poetry run pytest tests/test_instagram.py -k "not integration" -v

# Integration tests (live API via InstagramClient)
poetry run pytest tests/test_instagram.py -m integration -v
```

## API reference

- [Instagram Platform — Insights overview](https://developers.facebook.com/docs/instagram-platform/insights)
- [Instagram Platform — API reference](https://developers.facebook.com/docs/instagram-platform/reference)
- [IG User insights edge](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/insights)
- [IG Media insights edge](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-media/insights)
- [IG Comment reference](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-comment)
- [Error codes](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/error-codes)
- [Changelog](https://developers.facebook.com/docs/instagram-platform/changelog) — check here when a metric starts returning 400s
