# Facebook Analytics Pipeline

Pulls analytics for a single Facebook Page using the Graph API v25.0. Outputs PyArrow tables to the terminal. BigQuery loading is planned — the utility functions in `src/main.py` (`load_data_to_partitioned_bq`, etc.) will be wired up in a future step.

---

## Architecture

```
Page node
├── Page info        → page_id, name, fan_count, followers_count, talking_about_count
├── Page/insights    → page_id, metric, period, end_time, value
├── Page/posts       (all posts, paginated)
│   ├── Posts                      → post_id, page_id, message, created_time, post_type, permalink_url
│   ├── Post insights (scalar)     → post_id, metric, period, value
│   ├── Post reactions             → post_id, like, love, wow, haha, sad, angry
│   ├── Post activity by action    → post_id, action_type, unique_users
│   └── Post clicks by type        → post_id, click_type, clicks
└── Page/videos      (all videos, paginated)
    ├── Videos                     → video_id, page_id, title, description, created_time, length
    ├── Video insights (scalar)    → video_id, metric, period, value
    ├── Video reel reactions       → video_id, reaction_type, count
    ├── Video reel social actions  → video_id, action_type, count
    └── Video reel retention       → video_id, second, retention_pct
```

OOP model hierarchy lives in [models.py](models.py). PyArrow schemas and `to_arrow()` converters live in [schemas.py](schemas.py). The HTTP client and auth live in [api.py](api.py). Orchestration and CLI live in [main.py](main.py).

Auth utilities (`FileTokenCache`, `get_gcp_secret`) are shared with the YouTube pipeline via [src/authentication.py](../src/authentication.py).

---

## Secrets Required (GCP Secret Manager, project `gds-social-data`)

| Secret name | Description |
|---|---|
| `facebook_app_id` | Facebook App ID |
| `facebook_app_secret` | Facebook App Secret |
| `facebook_page_token` | JSON: `{"access_token": "..."}` — a page token |
| `facebook_page_id` | The numeric Page ID to query |



## generating long lived page tokens 
Useful guide - https://docs.squiz.net/funnelback/docs/latest/build/data-sources/facebook/facebook-page-access-token.html

You need to set up a facebook bussines App, i've created a video in the social medial shared drive -> social media data ingestion 

1) We generate a short term access token in the facebook graph api explorer tool using our business app. 
2) We then exchange that for a long term access token 
3) We use that long term access token to create a supposidly permiment page token.(There seems to be online debate, 
my page token expired after a month but I wasnt using it much).  


### Step 1 - Short lived with explorer tool
1) Generate a short lived user access token with the correct permissions

  i) You can use the page access token generated explorer tool or cli by the gouvk-social-f-b-app-bus 
  ii) ensure it has permissions: 
    pages_show_list
    business_management
    attribution_read
    pages_read_engagement
    pages_read_user_content
    pages_manage_engagement

### step 2 - long lived with bash sciprt  -https://developers.facebook.com/documentation/facebook-login/guides/access-tokens/get-long-lived
2) Use that short lived user access token to create a long lived access token 

Use can use gcloud to get secret values 
```
app_id=$(gcloud secrets versions access latest --secret="facebook_app_id") \
app_secret=$(gcloud secrets versions access latest --secret="facebook_app_secret") \
short_lived_access_token="<the_token_you_generated_with_tool>"
```

Just go the explorer tool -> 
i) Explore tooltools drop down 
ii) debug access token 
iii) insert short term access token 
iv) click "extend access token" to get long_term_access token 

I've also done it with the cli in the past 

i) curl -i -X GET "https://graph.facebook.com/v25.0/oauth/access_token?
    grant_type=fb_exchange_token&
    client_id={$app-id}&
    client_secret={$app-secret}&
    fb_exchange_token={$short_lived_access_token}"
  
### Step 3 - perm page access token
i) note the app scoped user id 
ii) add new app_scopred_user_id and long_term_access_token to https://graph.facebook.com/v12.0/APP-SCOPED-USER-ID/accounts?access_token=LONG-LIVED-ACCESS-TOKEN
iii) this will return your perm page token, which if you put back into the debugger shows "Expires:Never"
iv) upload this value to be the new gcp secret facebook_page_token


---

## Setup

```bash
# Install dependencies (from repo root)
poetry install

# Authenticate with GCP (to access Secret Manager)
gcloud auth application-default login

# Install pre-commit hooks (including the pre-push API test hook)
pre-commit install
pre-commit install --hook-type pre-push
```

---

## Running

```bash
# Default: last 1 days data, all posts and videos with insights
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
| `--start-date` | 1 day ago | Start of date range (YYYY-MM-DD) |
| `--end-date` | 1 day ago | Start of date range (YYYY-MM-DD) |
| `--run-post-data` / `--no-run-post-data` | on | Fetch posts (and optionally insights). `--no-run-post-data` skips all post work. |
| `--post-limit N` | all | Fetch only N posts and their insights |
| `--run-post-insights` / `--no-run-post-insights` | on | Fetch per-post insight calls (sub-flag of `--run-post-data`) |
| `--run-video-data` / `--no-run-video-data` | on | Fetch videos (and optionally insights). `--no-run-video-data` skips all video work. |
| `--video-limit N` | all | Fetch only N videos and their insights |
| `--run-video-insights` / `--no-run-video-insights` | on | Fetch per-video insight calls (sub-flag of `--run-video-data`) |

**Output**: 12 PyArrow tables printed as dicts to stdout — page info, page insights, posts, post insights (scalar), post reactions, post activity by action type, post clicks by type, videos, video insights (scalar), video reel reactions, video reel social actions, video reel retention.

---

## Testing

```bash
# Unit tests only (no API calls, no GCP credentials needed)
poetry run pytest tests/test_facebook.py -m "not integration" -x -q

# All tests including live API calls (requires GCP credentials + valid Facebook token)
poetry run pytest tests/test_facebook.py tests/test_facebook_http.py -m integration -v

# Run the pre-push metric checks manually without pushing
 pre-commit run facebook-integrated-tests --hook-stage pre-push
 pre-commit run facebook-api-http-tests --hook-stage pre-push

# to create a report outlining test coverage using pytest-cov use the --cov and --cov-report
poetry run pytest tests/test_facebook.py -m "not integration" -x -q --cov=facebook --cov-report=term-missing

# or with markdown for github actions 
poetry run pytest tests/test_facebook.py -m "not integration" -x -q --cov=facebook --cov=src --cov-report=term-missing --cov-report=markdown

# to output as part of github actions 
https://github.com/marketplace/actions/python-coverage 
https://github.com/marketplace/actions/pytest-coverage-comment -- this seems the most useful
https://chinsj.medium.com/continuous-integration-with-github-actions-using-poetry-pylint-and-pytest-with-coverage-check-857f4174e4f7

```

**Test module structure**: `test_facebook.py` and `test_facebook_http.py` are intentionally isolated — each builds its own client and fetches page/post/video IDs independently with no shared `conftest.py`. `test_facebook.py` tests the Python client and schema layer; `test_facebook_http.py` tests the raw HTTP endpoints directly with `requests.get`, providing an independent check that each API endpoint and metric string is valid.

---

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
| `post_reactions_by_type_total` | ~2025 | `/reactions` edge with `summary(total_count)` per type |
| `total_video_view_time` | unknown | N/A — page uses Reels; `total_video_*` metrics do not apply |

**Why fan metrics were removed**: Facebook's API now uses follower-based counts (`page_follows`, `page_daily_follows_unique`) as the primary audience metric; like/fan counts are no longer exposed as insights.

### Reference Documentation

- [Deprecated page insight metrics](https://developers.facebook.com/docs/platforminsights/page/deprecated-metrics/)
- [Pages API changelog](https://developers.facebook.com/docs/pages-api/changelog/)
- [Page insights reference (v25.0)](https://developers.facebook.com/docs/graph-api/reference/page/insights/)
- [Post insights reference (v25.0)](https://developers.facebook.com/docs/graph-api/reference/page-post/)

---

## Metrics Collected

### Page Insights (daily, `period=day`)

| Metric | Description |
|---|---|
| `page_media_view` | Total media impressions (replaces deprecated `page_impressions`) |
| `page_total_media_view_unique` | Unique media reach (replaces deprecated `page_impressions_unique`) |
| `page_post_engagements` | Reactions, comments, shares on posts |
| `page_daily_follows_unique` | New unique followers |
| `page_daily_unfollows_unique` | Unfollows |
| `page_views_total` | Total page views |
| `page_actions_post_reactions_total` | Total reactions across all post types |

### Page Insights (lifetime, `period=lifetime`)

| Metric | Description |
|---|---|
| `page_follows` | Cumulative follower count (replaces deprecated `page_fans`) |

### Post Insights (lifetime aggregate per post, via `/{post_id}/insights`)

| Metric | Description |
|---|---|
| `post_media_view` | Total impressions (replaces deprecated `post_impressions`) |
| `post_total_media_view_unique` | Unique reach (replaces deprecated `post_impressions_unique`) |
| `post_activity_by_action_type_unique` | Unique engaged users by action type (dict: likes, comments, shares, etc.) |
| `post_clicks` | Total link + photo clicks |
| `post_clicks_by_type` | Clicks broken down by type (dict) |

> **Note:** `post_reactions_by_type_total` was previously collected here but is deprecated and always returns `{}`. Post reactions are now fetched via the [`/reactions` edge](#reactions) instead.

### Reactions

Reactions are collected differently for posts vs videos because they come from different APIs with different data shapes.

#### Post reactions — `/reactions` edge (wide format)

Fetched as part of the `/{page_id}/posts` call using per-type field queries with `summary(total_count)`. Each reaction type is requested as a separate aliased field:

```
reactions.type(LIKE).limit(0).summary(total_count).as(like_reactions),
reactions.type(LOVE).limit(0).summary(total_count).as(love_reactions),
...
```

`limit(0)` suppresses the individual profile list; only the aggregate count is retained. Output is **wide format** — one row per post with a column per reaction type:

| Column | Description |
|---|---|
| `post_id` | Post ID |
| `like` | Count of Like reactions |
| `love` | Count of Love reactions |
| `wow` | Count of Wow reactions |
| `haha` | Count of Haha reactions |
| `sad` | Count of Sad reactions (API type: `SAD`) |
| `angry` | Count of Angry reactions (API type: `ANGRY`) |

Reference: [Object Reactions (Graph API v25.0)](https://developers.facebook.com/docs/graph-api/reference/v25.0/object/reactions) · [Post Reactions edge](https://developers.facebook.com/docs/graph-api/reference/post/reactions/)

#### Video reel reactions — `post_video_likes_by_reaction_type` metric (long format)

Fetched via `/{video_id}/video_insights?metric=post_video_likes_by_reaction_type&period=lifetime`. The API returns a dict of `{REACTION_TYPE: count}` entries (e.g. `{"REACTION_LIKE": 21, "REACTION_HAHA": 2}`). Output is **long format** — one row per reaction type per video, so a video with three reaction types produces three rows:

| Column | Description |
|---|---|
| `video_id` | Video ID |
| `reaction_type` | Reaction type string as returned by the API (e.g. `REACTION_LIKE`) |
| `count` | Number of reactions of this type |

Reference: [Video Insights metrics](https://developers.facebook.com/docs/graph-api/reference/video/video_insights/#metrics)

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

