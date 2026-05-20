# Facebook Analytics Pipeline

Pulls analytics for a single Facebook Page using the Graph API v25.0. Outputs PyArrow tables to the terminal. BigQuery loading is planned — the utility functions in `src/main.py` (`load_data_to_partitioned_bq`, etc.) will be wired up in a future step.

---

## Architecture

```
Page node
├── Page info        (id, name, fan_count, followers_count, talking_about_count)
├── Page/insights    (daily: impressions, reach, engagement, fans gained/lost, followers)
├── Page/posts       (all posts, paginated)
│   └── Post/insights per post (impressions, reach, engaged users, clicks, reactions)
└── Page/videos      (all videos, paginated)
    └── Video/video_insights per video (views, watch time, avg watch time, complete views)
```

OOP model hierarchy lives in [models.py](models.py). PyArrow schemas and `to_arrow()` converters live in [schemas.py](schemas.py). The HTTP client and auth live in [api.py](api.py). Orchestration and CLI live in [main.py](main.py).

Auth utilities (`FileTokenCache`, `get_gcp_secret`) are shared with the YouTube pipeline via [src/authentication.py](../src/authentication.py).

---

## Secrets Required (GCP Secret Manager, project `gds-social-data`)

| Secret name | Description |
|---|---|
| `facebook_app_id` | Facebook App ID |
| `facebook_app_secret` | Facebook App Secret |
| `facebook_long_live_token_json` | JSON: `{"access_token": "..."}` — a long-lived user/page token |
| `facebook_page_id` | The numeric Page ID to query |

The pipeline exchanges the long-lived token for a fresh access token on first run and caches it at `~/.config/facebook_exploration/token_cache.json`. Subsequent runs use the cached token until it expires (~60 days).

---

## Setup

```bash
# Install dependencies (from repo root)
poetry install

# Authenticate with GCP (to access Secret Manager)
gcloud auth application-default login
```

---

## Running

```bash
# Default: last 30 days, all posts and videos with insights
poetry run python -m facebook.main

# Custom date range
poetry run python -m facebook.main --start-date 2026-01-01 --end-date 2026-01-31

# Quick dev run — only 10 posts and 10 videos with their insights
poetry run python -m facebook.main --post-limit 10 --video-limit 10

# Page-level only — skip all post and video API calls (fastest)
poetry run python -m facebook.main --no-run-post-data --no-run-video-data

# Fetch posts but skip per-post insight calls
poetry run python -m facebook.main --no-run-post-insights

# Posts only, skip all video data
poetry run python -m facebook.main --no-run-video-data
```

**CLI flags summary:**

| Flag | Default | Description |
|---|---|---|
| `--start-date` | 30 days ago | Start of date range (YYYY-MM-DD) |
| `--end-date` | today | End of date range (YYYY-MM-DD) |
| `--run-post-data` / `--no-run-post-data` | on | Fetch posts (and optionally insights). `--no-run-post-data` skips all post work. |
| `--post-limit N` | all | Fetch only N posts and their insights |
| `--run-post-insights` / `--no-run-post-insights` | on | Fetch per-post insight calls (sub-flag of `--run-post-data`) |
| `--run-video-data` / `--no-run-video-data` | on | Fetch videos (and optionally insights). `--no-run-video-data` skips all video work. |
| `--video-limit N` | all | Fetch only N videos and their insights |
| `--run-video-insights` / `--no-run-video-insights` | on | Fetch per-video insight calls (sub-flag of `--run-video-data`) |

**Output**: PyArrow tables printed as dicts to stdout — one each for page info, page insights, posts, post insights, post reactions, videos, and video insights.

---

## Testing

```bash
# Unit tests only (no API calls, no GCP credentials needed)
poetry run pytest tests/test_facebook.py -m "not integration" -x -q

# All tests including live API calls (requires GCP credentials + valid Facebook token)
poetry run pytest tests/test_facebook.py -v
```

**Test module structure**: `test_facebook.py` and `test_facebook_http.py` are intentionally isolated — each builds its own client and fetches page/post/video IDs independently with no shared `conftest.py`. `test_facebook.py` tests the Python client and schema layer; `test_facebook_http.py` tests the raw HTTP endpoints directly with `requests.get`, providing an independent check that each API endpoint and metric string is valid.

---

<<<<<<< HEAD
=======
## API Constraints & Known Deprecations

### Query Limits

- **Day-period page insights**: maximum **93-day window** per request (`since`/`until`). Queries spanning longer periods must be split into multiple calls.
- **Pagination**: posts and videos use cursor-based pagination. The `_paginate()` helper follows `paging.cursors.after` automatically.

### Deprecated Metrics

Facebook has deprecated a number of page and post insight metrics across the v25.0 API. The metrics below are no longer valid and return `(#100) The value must be a valid insights metric`.

| Deprecated metric | Removed | Replacement used |
|---|---|---|
| `page_impressions` | Nov 2025 | `page_media_view` |
| `page_impressions_unique` | Jun 2025 | `page_total_media_view_unique` |
| `page_fan_adds` | Nov 2025 | *(no replacement — fan/like counts removed)* |
| `page_fan_removes` | Nov 2025 | *(no replacement)* |
| `page_fans` | Nov 2025 | `page_follows` |
| `post_impressions` | Nov 2025 | `post_media_view` |
| `post_impressions_unique` | Jun 2025 | `post_total_media_view_unique` |
| `post_engaged_users` | Mar 2024 | `post_engagements` |
| `total_video_view_time` | unknown | N/A — page uses Reels; `total_video_*` metrics do not apply |

**Why fan metrics were removed**: Facebook's API now uses follower-based counts (`page_follows`, `page_daily_follows_unique`) as the primary audience metric; like/fan counts are no longer exposed as insights.

### Reference Documentation

- [Deprecated page insight metrics](https://developers.facebook.com/docs/platforminsights/page/deprecated-metrics/)
- [Pages API changelog](https://developers.facebook.com/docs/pages-api/changelog/)
- [Page insights reference (v25.0)](https://developers.facebook.com/docs/graph-api/reference/page/insights/)
- [Post insights reference (v25.0)](https://developers.facebook.com/docs/graph-api/reference/page-post/)

---

>>>>>>> f5b9d98 (update facebook README with document Reels metrics, new CLI flags, and test module structure- Add CLI flags summary table covering --post-limit, --video-limit, --run-video-data- changes to python -m facebook.main use of -m flag- Include raw API response examples showing long-form video vs Reel metric differences)
## Metrics Collected

### Page Insights (daily)
| Metric | Description |
|---|---|
| `page_impressions` | Total content impressions |
| `page_impressions_unique` | Unique reach |
| `page_post_engagements` | Reactions, comments, shares |
| `page_fan_adds` | New page likes |
| `page_fan_removes` | Unlikes |
| `page_daily_follows_unique` | New followers |
| `page_daily_unfollows_unique` | Unfollows |
| `page_fans` | Cumulative page likes (lifetime snapshot) |

### Post Insights (lifetime aggregate per post)
| Metric | Description |
|---|---|
| `post_impressions` | Total impressions |
| `post_impressions_unique` | Unique reach |
| `post_engaged_users` | Unique engaged users |
| `post_clicks` | Link + photo clicks |
| `post_reactions_by_type_total` | Reactions flattened to: like, love, wow, haha, sorry, anger |

### Video Insights (lifetime aggregate per Reel, via `/{video_id}/video_insights`)

All videos on this page are Reels. Reels return `post_video_*` / `fb_reels_*` metrics — distinct from the
`total_video_*` metrics that traditional long-form videos return. The two sets are mutually exclusive.

Scalar metrics — stored as long-format rows in `VIDEO_INSIGHTS_SCHEMA` (`video_id`, `metric`, `period`, `value`):

| Metric | Description |
|---|---|
| `post_video_avg_time_watched` | Avg time (ms) the Reel was played per view |
| `post_video_view_time` | Total time (ms) the Reel was played, including replays |
| `post_impressions_unique` | Unique reach — people who saw the Reel at least once |
| `blue_reels_play_count` | Plays after an impression; excludes replays |
| `fb_reels_total_plays` | Total plays including replays |
| `fb_reels_replay_count` | Replays only |
| `post_video_followers` | Page follows within 24h of watching |

Dict metrics — each flattened into its own Arrow table:

| Metric | Arrow converter | Output schema |
|---|---|---|
| `post_video_likes_by_reaction_type` | `video_reel_reactions_to_arrow()` | `(video_id, reaction_type, count)` |
| `post_video_social_actions` | `video_reel_social_actions_to_arrow()` | `(video_id, action_type, count)` |
| `post_video_retention_graph` | `video_reel_retention_to_arrow()` | `(video_id, second, retention_pct)` |

All 10 metrics are parsed via `FacebookReelInsights` (in `models.py`), a typed Pydantic model with a
`model_validator` that flattens the raw Graph API `{"data": [...]}` response. Unknown metrics that appear
in future API versions are logged as warnings rather than causing errors.

### Not yet implemented

- Live video metrics

### Note on explorer tool usage
It will always return the most recent objects, regards of query modifiers you empty (since, unitl, order etc). Test by curling locally

### Since Aug 20, 2025 videos are treated as Reels and no video KPIs are available
- https://developers.facebook.com/community/threads/1115532177216249/


### Differences between Video and reel video_insights
- https://developers.facebook.com/docs/graph-api/reference/video/video_insights/#metrics


A useful indicator to differentiate before video_insights is the <video_node>/<permalink_edge>



Long-form video — /{video_id}/video_insights returns total_video_*:


{
  "data": [
    {
      "name": "total_video_views",
      "period": "lifetime",
      "values": [{ "value": 15234 }]
    },
    {
      "name": "total_video_avg_time_watched",
      "period": "lifetime",
      "values": [{ "value": 42000 }]
    },
    {
      "name": "total_video_complete_views_organic",
      "period": "lifetime",
      "values": [{ "value": 3201 }]
    },
    {
      "name": "total_video_impressions",
      "period": "lifetime",
      "values": [{ "value": 28000 }]
    },
    {
      "name": "total_video_views_by_age_bucket_and_gender",
      "period": "lifetime",
      "values": [{ "value": { "F.18-24": 1200, "M.25-34": 3400 } }]
    }
  ]
}
Reel — same endpoint, completely different metric names (as seen in your Explorer output):


{
  "data": [
    {
      "name": "fb_reels_total_plays",
      "period": "lifetime",
      "values": [{ "value": 2416 }]
    },
    {
      "name": "blue_reels_play_count",
      "period": "lifetime",
      "values": [{ "value": 2157 }]
    },
    {
      "name": "fb_reels_replay_count",
      "period": "lifetime",
      "values": [{ "value": 259 }]
    },
    {
      "name": "post_video_avg_time_watched",
      "period": "lifetime",
      "values": [{ "value": 4800 }]
    },
    {
      "name": "post_video_likes_by_reaction_type",
      "period": "lifetime",
      "values": [{ "value": { "REACTION_LIKE": 21, "REACTION_HAHA": 2 } }]
    }
  ]
}