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

Metrics 
- reach, views, likes, comments,saved,shares, total_interations

`likes`, `comments`, and `saved` are not valid for STORY media. The client swallows HTTP errors per-media-item (`get_media_insights` returns `[]` on failure) so a single story item in the feed does not break the pipeline.

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
python -m instagram.main --start-date 2026-05-01 --end-date 2026-05-31 --media-limit 10

# Skip per-media insights
python -m instagram.main --no-run-media-insights
```

Note: `follower_count` will fail with HTTP 400 if `--start-date` is more than 30 days ago. Use `--start-date` within the last 30 days when account insights are needed.

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
