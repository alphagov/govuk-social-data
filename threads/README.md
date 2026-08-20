# Threads module

Reads analytics data from the Threads Graph API and writes it to BigQuery as PyArrow tables. Same four-file pattern as `facebook/` and `instagram/`.

## Files

- `api.py`: `ThreadsClient` + `build_client()` + metric constants + response parsers
- `models.py`: Pydantic models
- `schemas.py`: PyArrow schemas and table converters
- `main.py`: CLI entry point, with `run_daily()` and `run_snapshot()`

`ThreadsClient` subclasses `src.meta.client.GraphClient` and sets `BASE_URL`. All transport (`_get` with retry/backoff, `_paginate`, `_next_page`, `_fetch_insight_items`) is inherited and never overridden here. `InsightMetricError` and `PaginationError` come from `src.meta.errors` and are re-exported from `api.py`.

## Two run modes

Only five metrics are reported per day by this API. Everything else is lifetime-to-date, so it can only be captured as "what is true right now". That split is why there are two modes.

**`--mode daily`** fetches the time-windowed account metrics (views, likes, replies, reposts, quotes) and partitions on `--target-date`, the day being measured.

**`--mode snapshot`** fetches profile, posts, post insights, replies, follower count and follower demographics. It partitions on `--snapshot-date`, the day it was read.

Both are intended to run daily.

## Authentication

Secrets live in GCP Secret Manager, project `gds-social-data`:

- `threads_long_lived_access_token`: the user token, stored as a plain string
- `threads_user_id`: the Threads user ID
- `threads_app_id` and `threads_app_secret`: used only by the token scripts, never by the pipeline

The token must be a user token with `threads_basic`, `threads_manage_insights` and `threads_read_replies`. App credentials cannot stand in for it.

It expires 60 days after issue. If it is not refreshed inside that window it dies permanently and a human has to run the authorization flow again.

```bash
# Mint a new token (needs a human)
poetry run python -m src.scripts.quickstart_scripts.threads_oauth_quickstart

# Refresh the existing token, writing a new secret version
poetry run python -m src.scripts.quickstart_scripts.threads_refresh_token
```

## API base URL

`https://graph.threads.net/v1.0`

A different host from Facebook and Instagram, which is why `BASE_URL` is a class attribute on `GraphClient` rather than a module constant.

## Metric constants

**`ACCOUNT_TIME_SERIES_METRICS`** = `views`. Fetched from `/{user_id}/threads_insights` with `since` and `until` as unix seconds. Returns `values: [{value, end_time}]`, so one row per day.

**`ACCOUNT_TOTAL_VALUE_METRICS`** = `likes`, `replies`, `reposts`, `quotes`. Same endpoint, but the response is `total_value: {value}`. One number for the whole window, with no `end_time`.

**`ACCOUNT_LIFETIME_METRICS`** = `followers_count`. Same endpoint with no `since` or `until`. Snapshot mode only, since the API keeps no history.

**`MEDIA_METRICS`** = `views`, `likes`, `replies`, `reposts`, `quotes`, `shares`, `clicks`. Fetched from `/{media_id}/insights`. This edge takes no `period` parameter. `clicks` counts link clicks on the post and has read 0 on every GOV.UK post sampled so far.

**`DEMOGRAPHIC_BREAKDOWNS`** = `age`, `gender`, `country`, `city`. Fetched from `/{user_id}/threads_insights?metric=follower_demographics&breakdown=...`, no `since` or `until`.

- `age` returns 7 buckets, 13-17 up to 65+
- `gender` returns F, M and U
- `country` returns ISO codes, capped at 45 entries and unordered
- `city` is also capped at 45 and unordered, and covers well under half the follower base

One request goes out per breakdown. The edge does accept several at once, but then returns their cross product (`dimension_keys ["age","gender"]` giving `dimension_values ["25-34","F"]`), which does not fit the one-dimension-per-row grain of the table. A breakdown that fails is logged and skipped so the others still land.

Below 100 followers the edge returns an empty `{}` instead of a 400. `_parse_demographics` treats that as no data and returns no rows.

## Two API behaviours worth knowing

**`clicks` is a media metric, not an account one.** Meta documents it at account level, and the API's own validation error lists it as valid there, but every account-level request is refused with `Unsupported request - method type: get`. It works per media. `tests/test_threads_http.py` pins that 400 so nobody re-adds it.

**Insight retention is a rolling 730 days.** The docs imply a fixed start date of 2024-04-13, but the API says `since param is not valid. Metrics data is available for the last 2 years`. Measured live: 729 days back returns 200, 730 returns 400. `_to_unix` refuses an out of range date before making the request, so a bad `--target-date` raises a readable `ValueError` instead of an opaque Graph 400 mid-run.

## BigQuery tables

Written to dataset `govuk_threads` in project `gds-social-data`. Every table is partitioned on `target_date` and gets `target_date` (DATE) and `ingestion_timestamp` (TIMESTAMP) appended by `upload_partitioned`. Each run overwrites its partition rather than merging into it.

### threads_account_insights (daily mode)

One row per metric per bucket.

- `threads_user_id` STRING
- `metric` STRING: views, likes, replies, reposts, quotes
- `period` STRING: always "day"
- `end_time` TIMESTAMP: end of the window the value covers, NULL for the total_value metrics
- `value` INT64

`end_time` is 07:00 UTC because Meta anchors these buckets to midnight `America/Los_Angeles`. A bucket labelled with the target date therefore measures the Pacific day before it. `instagram_account_insights` stores the same labelling.

The edge answers a one day window with the requested day *and* the day before it. `_clip_to_window` drops the extra. Without it the same metric and `end_time` would appear in two partitions, which per-partition overwrite cannot dedupe.

### threads_profile (snapshot mode)

One row per run.

- `threads_user_id` STRING
- `username` STRING
- `name` STRING
- `biography` STRING
- `profile_picture_url` STRING: signed CDN URL, expires, do not treat as stable
- `is_verified` BOOL

### threads_media (snapshot mode)

One row per post fetched. `target_date` is the run date, so use `timestamp` for the publish date.

- `threads_user_id` STRING
- `media_id` STRING
- `media_type` STRING: TEXT_POST, IMAGE, VIDEO, CAROUSEL_ALBUM, AUDIO, REPOST_FACADE
- `media_product_type` STRING: THREADS
- `permalink` STRING
- `text` STRING
- `timestamp` TIMESTAMP: when the post was published
- `shortcode` STRING
- `is_quote_post` BOOL
- `has_replies` BOOL
- `link_attachment_url` STRING
- `gif_url` STRING
- `alt_text` STRING
- `poll_attachment` STRING: raw JSON, read it with `JSON_VALUE`
- `topic_tag` STRING: the tag name as returned

### threads_media_insights (snapshot mode)

One row per post per metric, lifetime cumulative at ingestion. Join to `threads_media` on `media_id`.

- `media_id` STRING
- `metric` STRING: see `MEDIA_METRICS` above
- `period` STRING: always "lifetime"
- `value` INT64: metrics the API returns with no value are omitted rather than written as NULL

### threads_replies (snapshot mode)

One row per reply the account has made.

- `threads_user_id` STRING
- `reply_id` STRING
- `media_type` STRING
- `text` STRING
- `timestamp` TIMESTAMP
- `shortcode` STRING
- `permalink` STRING
- `has_replies` BOOL
- `is_reply` BOOL
- `root_post_id` STRING: top of the thread, NULL when the reply is itself a root post
- `replied_to_id` STRING: the post directly replied to, NULL as above
- `hide_status` STRING: NOT_HUSHED, UNHUSHED, HIDDEN, COVERED, BLOCKED, RESTRICTED

### threads_account_lifetime (snapshot mode)

One row per lifetime metric. Currently just `followers_count`, which the daily snapshot turns into a history.

- `threads_user_id` STRING
- `metric` STRING
- `value` INT64

### threads_follower_demographics (snapshot mode)

One row per breakdown per dimension value.

- `threads_user_id` STRING
- `breakdown` STRING: age, gender, country, city
- `dimension_value` STRING: 25-34, F, GB, London
- `value` INT64: follower count in that bucket

## Useful queries

Daily account metrics, pivoted:

```sql
SELECT
  target_date,
  MAX(IF(metric = 'views',   value, NULL)) AS views,
  MAX(IF(metric = 'likes',   value, NULL)) AS likes,
  MAX(IF(metric = 'replies', value, NULL)) AS replies,
  MAX(IF(metric = 'reposts', value, NULL)) AS reposts,
  MAX(IF(metric = 'quotes',  value, NULL)) AS quotes
FROM `gds-social-data.govuk_threads.threads_account_insights`
GROUP BY target_date
ORDER BY target_date DESC
```

Follower growth out of the daily snapshot:

```sql
SELECT
  target_date,
  value AS followers,
  value - LAG(value) OVER (ORDER BY target_date) AS daily_change
FROM `gds-social-data.govuk_threads.threads_account_lifetime`
WHERE metric = 'followers_count'
ORDER BY target_date DESC
```

Top follower countries, since the API does not return them ranked:

```sql
SELECT dimension_value AS country, value AS followers
FROM `gds-social-data.govuk_threads.threads_follower_demographics`
WHERE breakdown = 'country'
  AND target_date = (SELECT MAX(target_date) FROM `gds-social-data.govuk_threads.threads_follower_demographics`)
ORDER BY followers DESC
LIMIT 10
```

## Do not add these

- `clicks` at account level. Documented by Meta, refused by the API. It is in `MEDIA_METRICS` instead.
- `follower_demographics` with multiple breakdowns. Accepted, but returns a cross product that does not fit the table.
- Shares brought by a post, or follows attributed to a post. The API does not support either at all.

## Running locally

```bash
# Yesterday's account metrics
python -m threads.main --mode daily

# A specific day, into the dev dataset
python -m threads.main --mode daily --target-date 2026-08-18 --dataset govuk_threads_dev

# Today's snapshot
python -m threads.main --mode snapshot

# A quick snapshot, skipping the slower parts
python -m threads.main --mode snapshot --media-limit 10 --reply-limit 10 --no-run-demographics --dataset govuk_threads_dev
```

Flags:

- `--mode`: daily or snapshot, defaults to daily
- `--target-date`: daily mode, the day fetched and its partition, defaults to yesterday
- `--snapshot-date`: snapshot mode, the partition written to, defaults to today
- `--dataset`: defaults to `govuk_threads`
- `--media-limit` and `--reply-limit`: default to no limit
- `--run-media-insights`, `--run-replies`, `--run-demographics`: on by default, prefix with `--no-` to skip

A `--target-date` more than 730 days ago raises `ValueError` before any request is made.

## Running tests

```bash
# HTTP endpoint validation, live API, no Python client involved
poetry run pytest tests/test_threads_http.py -m integration -v

# Unit tests, no credentials needed
poetry run pytest tests/test_threads.py -m "not integration" -v

# Integration tests, live API through ThreadsClient
poetry run pytest tests/test_threads.py -m integration -v
```

`tests/test_threads_http.py` imports its field and metric constants from `api.py`, so the two cannot drift. A failure there is fixed by editing the constants, never by editing the test.

## API reference

- [Threads API overview](https://developers.facebook.com/docs/threads)
- [Access tokens and permissions](https://developers.facebook.com/docs/threads/get-started/get-access-tokens-and-permissions)
- [Long-lived tokens](https://developers.facebook.com/docs/threads/get-started/long-lived-tokens)
- [Threads media](https://developers.facebook.com/docs/threads/threads-media)
- [Insights](https://developers.facebook.com/docs/threads/insights)
- [Reply management](https://developers.facebook.com/docs/threads/reply-management)
- [Changelog](https://developers.facebook.com/docs/threads/changelog), worth checking when a metric starts returning 400s
