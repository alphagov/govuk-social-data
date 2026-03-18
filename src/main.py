# -*- coding: utf-8 -*-

import json
import time 
import logging
import os
import re
from typing import Any, Callable
import googleapiclient.discovery
from googleapiclient.discovery import Resource
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.cloud import secretmanager
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import bigquery


logger = logging.getLogger(__name__)

PROJECT_ID = "gds-social-data"

CLIENT_ID_STRING_SECRET = "youtube-processing-client-id-web-app"
CLIENT_SECRET_STRING_SECRET = "youtube-processing-client-secret-web-app"
CHANNEL_ID = "UCSNK6abAoM6Kj0SkHOStNLg"
TOKEN_SECRET_VERSION= "7"  # token version or "latest"
TOKEN_JSON_SECRET = "youtube-processing-web-client-token-json"
TOKEN_CACHE_PATH = os.path.expanduser("~/.config/youtube_exploration/token_cache.json")


youtube_traffic_sources = {
    "ADVERTISING": "The viewer was referred to the video by an advertisement. The insightTrafficSourceDetail field identifies the type of advertisement.",
    "ANNOTATION": "Viewers reached the video by clicking on an annotation in another video.",
    "CAMPAIGN_CARD": "Views originated from claimed, user-uploaded videos used to promote content. Supported for content owner reports only.",
    "END_SCREEN": "The views were referred from the end screen of another video.",
    "EXT_URL": "The video views were referred from a link on another website, including Google Search results. The insightTrafficSourceDetail field identifies the web page.",
    "HASHTAGS": "Views originated from VOD hashtag pages or Shorts hashtag pivot pages.",
    "LIVE_REDIRECT": "The video views were referred from Live Redirects.",
    "NO_LINK_EMBEDDED": "The video was embedded on another website when it was viewed.",
    "NO_LINK_OTHER": "Direct traffic or traffic from mobile apps where YouTube did not identify a referrer.",
    "NOTIFICATION": "The video views were referred from an email or notification from YouTube.",
    "PLAYLIST": "The video views occurred while the video was being played as part of a playlist or from the playlist page.",
    "PRODUCT_PAGE": "The video views were referred from a Product page.",
    "PROMOTED": "The video views were referred from an unpaid YouTube promotion, such as the 'Spotlight Videos' page.",
    "RELATED_VIDEO": "The video views were referred from a related video listing. The insightTrafficSourceDetail field specifies the referring video ID.",
    "SHORTS": "The viewer was referred by swiping vertically from the previous video in the Shorts player.",
    "SOUND_PAGE": "Views originated from Shorts sound pivot pages.",
    "SUBSCRIBER": "Views referred from homepage feeds or subscription features. Details specify the specific feed items.",
    "YT_CHANNEL": "The video views occurred on a channel page. The insightTrafficSourceDetail field specifies the channel ID.",
    "YT_OTHER_PAGE": "Views referred from links on YouTube pages other than search or related videos.",
    "YT_SEARCH": "Views referred from YouTube search results. The insightTrafficSourceDetail field specifies the search term used.",
    "VIDEO_REMIXES": "Views originating from videos that used the content as a sample or remix."
}


# Daily reports

def get_user_activity_single_video_single_day_config(video_id:str) -> dict[str, Any]:
    """Returns the configuration for retrieving user activity metrics for a single video on a daily basis.
    This function has been replaced by generic_report_config but is useful for troubleshooting"""
    return {
        "dimensions":"day",
        "metrics"   :"engagedViews,views,redViews,comments,likes,dislikes,shares,estimatedMinutesWatched,estimatedRedMinutesWatched,averageViewDuration,averageViewPercentage,annotationClickThroughRate,annotationCloseRate,annotationImpressions,annotationClickableImpressions,annotationClosableImpressions,annotationClicks,annotationCloses,cardClickRate,cardTeaserClickRate,cardImpressions,cardTeaserImpressions,cardClicks,cardTeaserClicks",
        "filters"   : f"video=={video_id}"
    }   

def get_playback_details_single_video_single_day_config(video_id:str) -> dict[str, Any]:
    """Returns the configuration for retrieving playback details metrics for a single video on a daily basis.
    Note the creatorContentType, informs us if its a video,short or live stream, this is important as some metrics are only available for certain content types, e.g. engagedViews is not available for shorts."""
    return {
        "dimensions":"day,creatorContentType",
        "metrics"   :"engagedViews,views,redViews,estimatedMinutesWatched,estimatedRedMinutesWatched,averageViewDuration,averageViewPercentage",
        "filters"   : f"video=={video_id}"
    }

def get_playback_locations_single_video_single_day_config(video_id:str) -> dict[str, Any]:
    """Returns the configuration for retrieving playback location metrics for a single video on a daily basis.
    Note that insightPlaybackLocationType dimension informs us where the video was played e.g. on YouTube watch page, embedded on another website, etc. This is important as some metrics are only available for certain playback locations, e.g. engagedViews is not available for embedded playback location type.
    https://developers.google.com/youtube/analytics/channel_reports?_gl=1*a9sxfc*_up*MQ..*_ga*MTMwNjgyMDgyLjE3NzE4NjMzMDg.*_ga_SM8HXJ53K2*czE3NzE4NjMzMDgkbzEkZzAkdDE3NzE4NjMzMDgkajYwJGwwJGgw
    """
    return {
        "dimensions":"day,insightPlaybackLocationType,creatorContentType",
        "metrics"   :"engagedViews,views,estimatedMinutesWatched",
        "filters"   : f"video=={video_id}"
    }

def get_traffic_sources_single_video_single_day_config(video_id:str) -> dict[str, Any]:
    """
    note that traffic sources requires insightTrafficSourcesTypes allows single day, traffic sources detials does not allow day dimension
    https://developers.google.com/youtube/analytics/v1/dimsmets/dims?_gl=1*1cm8ijd*_up*MQ..*_ga*ODQxMTE1NjkzLjE3NzI2NDI5NjA.*_ga_SM8HXJ53K2*czE3NzI2NDI5NTkkbzEkZzAkdDE3NzI2NDI5NTkkajYwJGwwJGgw#Traffic_Source_Dimensions 
    We can not query more then 50,000 day*video records at a time https://developers.google.com/youtube/analytics/channel_reports#traffic-source-reports 
    as we have 600 videos max, we should not query mre then 90 days a time for traffic course. I assume Dimensions are not effected"""
    return {
        "dimensions":"day,insightTrafficSourceType,creatorContentType",
        "metrics"   :"engagedViews,views,estimatedMinutesWatched",
        "filters"   : f"video=={video_id}"
}

def get_operating_system_and_device_type_config(video_id:str) -> dict[str, Any]:
    """Returns the configuration for retrieving OS and device type metrics for a single video on a daily basis."""
    return {
        "dimensions":"day,deviceType,operatingSystem",
        "metrics"   :"engagedViews,views,estimatedMinutesWatched",
        "filters"   :f"video=={video_id}"
    }

## Reports which do not allow day dimension
# note that traffic sources requires insightTrafficSourcesTypes allows single day, traffic sources detials does not allow day dimension
def get_viewer_demographics_config(video_id:str) -> dict[str, Any]:
    """Returns the configuration for retrieving viewer demographics (age group and gender) for a single video."""
    return {
        "dimensions":"ageGroup,gender",
        "metrics"   :"viewerPercentage",
        "filters"   :f"video=={video_id}"
    }

def get_engagement_and_content_sharing_config(video_id:str) -> dict[str, Any]:
    """Returns the configuration for retrieving content sharing metrics by sharing service for a single video."""
    return {
        "dimensions":"sharingService",
        "metrics"   :"shares",
        "filters"   :f"video=={video_id}"
    }

## Reports not implemented
# playback location detail as it only shows information for insightPlaybackLocationType==Embedded and does not allow dim by day
# Traffic source detail does not allow dim by day 
# audience retiontion (wht part of the videos they watched)


VIDEO_ID_BQ_SCHEMA = {
    "video_id":                         pa.string(),
    "video_title":                      pa.string(),
}

VIDEO_METADATA_BQ_SCHEMA = {
    "video_id":                         pa.string(),
    "title":                            pa.string(),
    "publishedAt":                      pa.string(),
    "description":                      pa.string(),
    "tags":                             pa.list_(pa.string()),
    "categoryId":                       pa.string(),
    "categoryTitle":                    pa.string(),
    "channelTitle":                     pa.string(),
    "channelId":                        pa.string(),
    "defaultLanguage":                  pa.string(),
    "defaultAudioLanguage":             pa.string(),
    "duration":                         pa.string(),
    "duration_in_seconds":              pa.int64(),
    "definition":                       pa.string(),
    "caption":                          pa.string(),
    "regionRestriction_allowed":        pa.list_(pa.string()),
    "regionRestriction_blocked":        pa.list_(pa.string()),
    "viewCount":                        pa.int64(),
    "likeCount":                        pa.int64(),
    "commentCount":                     pa.int64(),
    "favoriteCount":                    pa.int64(),
    "privacyStatus":                    pa.string(),
    "madeForKids":                      pa.bool_(),
    "embeddable":                       pa.bool_(),
}

USER_ACTIVITY_BQ_SCHEMA = {
    "day":                              pa.date32(),
    "engagedViews":                     pa.int64(),
    "views":                            pa.int64(),
    "redViews":                         pa.int64(),
    "comments":                         pa.int64(),
    "likes":                            pa.int64(),
    "dislikes":                         pa.int64(),
    "shares":                           pa.int64(),
    "estimatedMinutesWatched":          pa.float64(),
    "estimatedRedMinutesWatched":       pa.float64(),
    "averageViewDuration":              pa.float64(),
    "averageViewPercentage":            pa.float64(),
    "annotationClickThroughRate":       pa.float64(),
    "annotationCloseRate":              pa.float64(),
    "annotationImpressions":            pa.int64(),
    "annotationClickableImpressions":   pa.int64(),
    "annotationClosableImpressions":    pa.int64(),
    "annotationClicks":                 pa.int64(),
    "annotationCloses":                 pa.int64(),
    "cardClickRate":                    pa.float64(),
    "cardTeaserClickRate":              pa.float64(),
    "cardImpressions":                  pa.int64(),
    "cardTeaserImpressions":            pa.int64(),
    "cardClicks":                       pa.int64(),
    "cardTeaserClicks":                 pa.int64(),
}

PLAYBACK_DETAILS_BQ_SCHEMA = {
    "day":                              pa.date32(),
    "creatorContentType":               pa.string(),
    "engagedViews":                     pa.int64(),
    "views":                            pa.int64(),
    "redViews":                         pa.int64(),
    "estimatedMinutesWatched":          pa.float64(),
    "estimatedRedMinutesWatched":       pa.float64(),
    "averageViewDuration":              pa.float64(),
    "averageViewPercentage":            pa.float64(),
}

PLAYBACK_LOCATION_BQ_SCHEMA = {
    "day":                              pa.date32(),
    "insightPlaybackLocationType":      pa.string(),
    "creatorContentType":               pa.string(),
    "engagedViews":                     pa.int64(),
    "views":                            pa.int64(),
    "estimatedMinutesWatched":          pa.float64(),
}

TRAFFIC_SOURCES_BQ_SCHEMA = {
    "day":                              pa.date32(),
    "insightTrafficSourceType":         pa.string(),
    "creatorContentType":               pa.string(),
    "engagedViews":                     pa.int64(),
    "views":                            pa.int64(),
    "estimatedMinutesWatched":          pa.float64(),
}

OPERATING_SYSTEM_AND_DEVICE_TYPE_BQ_SCHEMA = {
    "day":                              pa.date32(),
    "deviceType":                       pa.string(),
    "operatingSystem":                  pa.string(),
    "engagedViews":                     pa.int64(),
    "views":                            pa.int64(),
    "estimatedMinutesWatched":          pa.float64(),
}

VIEWER_DEMOGRAPHICS_BQ_SCHEMA = {
    "ageGroup":                         pa.string(),
    "gender":                           pa.string(),
    "viewerPercentage":                 pa.float64(),
}

ENGAGEMENT_AND_CONTENT_SHARING_BQ_SCHEMA = {
    "sharingService":                   pa.string(),
    "shares":                           pa.int64(),
}

# generated using GB region code + snippet https://developers.google.com/youtube/v3/docs/videoCategories/list
VIDEO_CATEGORY_ID_TO_TITLE = {
    "1":  "Film & Animation",
    "2":  "Autos & Vehicles",
    "10": "Music",
    "15": "Pets & Animals",
    "17": "Sports",
    "18": "Short Movies",
    "19": "Travel & Events",
    "20": "Gaming",
    "21": "Videoblogging",
    "22": "People & Blogs",
    "23": "Comedy",
    "24": "Entertainment",
    "25": "News & Politics",
    "26": "Howto & Style",
    "27": "Education",
    "28": "Science & Technology",
    "30": "Movies",
    "31": "Anime/Animation",
    "32": "Action/Adventure",
    "33": "Classics",
    "34": "Comedy",
    "35": "Documentary",
    "36": "Drama",
    "37": "Family",
    "38": "Foreign",
    "39": "Horror",
    "40": "Sci-Fi/Fantasy",
    "41": "Thriller",
    "42": "Shorts",
    "43": "Shows",
    "44": "Trailers",
}

### functions for single purpose reports 


def get_video_metadata(youtube_dataapi_v3_service: Resource, video_ids: list[str]) -> pa.Table:
    """Fetches comprehensive metadata for a list of video IDs using the YouTube Data API v3.
    Returns a pa.Table with snippet, contentDetails, statistics, and status fields.
    The Data API accepts at most 50 IDs per request.
    """
    cols = {key: [] for key in VIDEO_METADATA_BQ_SCHEMA}
    for chunk in chunk_video_ids(set(video_ids), chunk_size=50):
        response = youtube_dataapi_v3_service.videos().list(
            part="snippet,contentDetails,statistics,status",
            id=",".join(chunk),
        ).execute()
        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            content = item.get("contentDetails", {})
            stats   = item.get("statistics", {})
            status  = item.get("status", {})
            region  = content.get("regionRestriction", {})

            cols["video_id"].append(item["id"])
            cols["title"].append(snippet.get("title"))
            cols["publishedAt"].append(snippet.get("publishedAt"))
            cols["description"].append(snippet.get("description"))
            cols["tags"].append(snippet.get("tags") or [])
            cols["categoryId"].append(snippet.get("categoryId"))
            cols["categoryTitle"].append(VIDEO_CATEGORY_ID_TO_TITLE.get(snippet.get("categoryId"), "Unknown"))
            cols["channelTitle"].append(snippet.get("channelTitle"))
            cols["channelId"].append(snippet.get("channelId"))
            cols["defaultLanguage"].append(snippet.get("defaultLanguage"))
            cols["defaultAudioLanguage"].append(snippet.get("defaultAudioLanguage"))
            cols["duration"].append(content.get("duration"))
            cols["duration_in_seconds"].append(_iso8601_duration_to_seconds(content.get("duration")))
            cols["definition"].append(content.get("definition"))
            cols["caption"].append(content.get("caption"))
            cols["regionRestriction_allowed"].append(region.get("allowed") or [])
            cols["regionRestriction_blocked"].append(region.get("blocked") or [])
            cols["viewCount"].append(int(stats["viewCount"]) if "viewCount" in stats else None)
            cols["likeCount"].append(int(stats["likeCount"]) if "likeCount" in stats else None)
            cols["commentCount"].append(int(stats["commentCount"]) if "commentCount" in stats else None)
            cols["favoriteCount"].append(int(stats["favoriteCount"]) if "favoriteCount" in stats else None)
            cols["privacyStatus"].append(status.get("privacyStatus"))
            cols["madeForKids"].append(status.get("madeForKids"))
            cols["embeddable"].append(status.get("embeddable"))
    return pa.table(cols)

def get_top_200_videos_for_date_range(youtube_service: Resource, start_date: str, end_date: str, days_delta: int = 7) -> pa.Table:
    """Returns unique video IDs with greater than 0 views for the given date range.
    Note: The YouTube Analytics API has a maximum of 200 rows per request, so we query multiple date ranges to discover more videos.
    """
    sample_date_ranges = generate_date_ranges(start_date=start_date, end_date=end_date, days_delta=days_delta)
    # sample_date_ranges = [("2021-01-01", "2021-01-08"), ("2021-01-08", "2021-01-15")] # for testing - comment out to run full date range

    video_ids: set[str] = set()
    for range_start, range_end in sample_date_ranges:
        logger.debug("Querying range %s to %s", range_start, range_end)
        response = youtube_service.reports().query(
            ids="channel==MINE",
            startDate=range_start,
            endDate=range_end,
            metrics="views",
            maxResults=200,
            dimensions="video",
            sort="-views",
        ).execute()
        rows = response.get("rows", [])
        video_ids.update(row[0] for row in rows)
        if len(rows) >= 200:
            logger.warning("Retrieved %d rows (max per request). Consider shortening the date range.", len(rows))

    logger.info("Found %d unique videos", len(video_ids))
    return pa.table({"video": list(video_ids)})


def get_user_activity_single_video_single_day(youtube_service: Resource, start_date: str, end_date: str, video_id: str) -> pa.Table:
    """Queries daily user activity metrics for a single video and returns the results as a pa.Table."""
    response = youtube_service.reports().query(
        dimensions  = "day",
        ids         ="channel==MINE",
        startDate   = start_date,
        endDate     = end_date,
        filters     = f"video=={video_id}",
        metrics     = "engagedViews,views,redViews,comments,likes,dislikes,shares,estimatedMinutesWatched,estimatedRedMinutesWatched,averageViewDuration,averageViewPercentage,annotationClickThroughRate,annotationCloseRate,annotationImpressions,annotationClickableImpressions,annotationClosableImpressions,annotationClicks,annotationCloses,cardClickRate,cardTeaserClickRate,cardImpressions,cardTeaserImpressions,cardClicks,cardTeaserClicks"
    ).execute()

    headers = [col["name"] for col in response.get("columnHeaders", [])]
    rows = response.get("rows", [])
    columns = dict(zip(headers, zip(*rows))) if rows else {h: [] for h in headers}
    return pa.table(columns)

def get_traffic_source_data_periodic(youtube_service: Resource, start_date: str, end_date: str, video_id: str) -> pa.Table:
    """Queries daily user activity metrics for a single video and returns the results as a pa.Table."""
    response = youtube_service.reports().query(
        dimensions  = "day",
        ids         ="channel==MINE",
        filters     = f"video=={video_id}",
        metrics     = "engagedViews,views,redViews,comments,likes,dislikes,shares,estimatedMinutesWatched,estimatedRedMinutesWatched,averageViewDuration,averageViewPercentage,annotationClickThroughRate,annotationCloseRate,annotationImpressions,annotationClickableImpressions,annotationClosableImpressions,annotationClicks,annotationCloses,cardClickRate,cardTeaserClickRate,cardImpressions,cardTeaserImpressions,cardClicks,cardTeaserClicks"
    ).execute()

    headers = [col["name"] for col in response.get("columnHeaders", [])]
    rows = response.get("rows", [])
    columns = dict(zip(headers, zip(*rows))) if rows else {h: [] for h in headers}
    return pa.table(columns)

def get_video_titles(youtube_dataapi_v3_service: Resource, video_ids: list[str]) -> pa.Table:
    """Fetches video titles for a list of video IDs using the YouTube Data API v3.
    Returns a pa.Table with columns: video_id, video_title.
    The Data API accepts at most 50 IDs per request.
    """
    video_id_col, video_title_col = [], []
    for chunk in chunk_video_ids(set(video_ids), chunk_size=50):
        response = youtube_dataapi_v3_service.videos().list(
            part="snippet",
            id=",".join(chunk),
        ).execute()
        for item in response.get("items", []):
            video_id_col.append(item["id"])
            video_title_col.append(item["snippet"]["title"])
    return pa.table({"video_id": video_id_col, "video_title": video_title_col})

# authentication functions 


def _save_token_cache(credentials: Credentials) -> None:
    """Persists OAuth2 credentials to the local token cache file."""
    os.makedirs(os.path.dirname(TOKEN_CACHE_PATH), exist_ok=True)
    with open(TOKEN_CACHE_PATH, "w") as f:
        json.dump({
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
        }, f)

def _load_token_cache(client_id: str, client_secret: str) -> Credentials | None:
    """Loads OAuth2 credentials from the local token cache, refreshing if expired. Returns None if no cache exists."""
    if not os.path.exists(TOKEN_CACHE_PATH):
        return None
    with open(TOKEN_CACHE_PATH) as f:
        data = json.load(f)
    expiry = datetime.fromisoformat(data["expiry"]) if data.get("expiry") else None
    credentials = Credentials(
        token=data.get("access_token"),
        refresh_token=data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/yt-analytics.readonly","https://www.googleapis.com/auth/youtube.readonly"],
        expiry=expiry,
    )
    if not credentials.valid:
        if not credentials.refresh_token:
            return None
        try:
            credentials.refresh(Request())
        except Exception:
            logger.warning("Failed to refresh cached token, will re-fetch from Secret Manager")
            return None
        _save_token_cache(credentials)
    return credentials


def get_gcp_secret(secret_name: str, secret_version: str = "latest", project_id: str = PROJECT_ID) -> str:
    """Retrieves a secret value from GCP Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/{secret_version}"
    payload = client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")
    return payload

def _build_service(api_name: str, version: str, scopes: list[str]) -> Resource:
    """Builds an authenticated Google API service client with OAuth2 credentials."""
    client_id = get_gcp_secret(CLIENT_ID_STRING_SECRET)
    client_secret = get_gcp_secret(CLIENT_SECRET_STRING_SECRET)

    credentials = _load_token_cache(client_id, client_secret)
    if credentials is None:
        # Cold start: pull from Secret Manager.
        # expires_in in the stored token is relative to issuance time (unknown), so
        # force a refresh immediately to get a fresh access token with a real expiry.
        token_info = json.loads(get_gcp_secret(TOKEN_JSON_SECRET, TOKEN_SECRET_VERSION))
        credentials = Credentials(
            token=None,  # force refresh — stored access token may be stale
            refresh_token=token_info.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
        )
        try:
            credentials.refresh(Request())
        except Exception:
            logger.warning(
                "Refresh failed with secret version %s, retrying with latest version",
                TOKEN_SECRET_VERSION,
            )
            token_info = json.loads(get_gcp_secret(TOKEN_JSON_SECRET, "latest"))
            credentials = Credentials(
                token=None,
                refresh_token=token_info.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=scopes,
            )
            credentials.refresh(Request())
        _save_token_cache(credentials)

    return googleapiclient.discovery.build(api_name, version, credentials=credentials)

def get_youtube_analytics_api_v2_service() -> Resource:
    """Builds and returns an authenticated YouTube Analytics API v2 service client."""
    return _build_service("youtubeAnalytics", "v2", ["https://www.googleapis.com/auth/yt-analytics.readonly"])

def get_youtube_data_api_v3_service() -> Resource:
    """Builds and returns an authenticated YouTube Data API v3 service client."""
    return _build_service("youtube", "v3", ["https://www.googleapis.com/auth/youtube.readonly"])


## helper functions 

def _with_video_id(schema: dict[str, pa.DataType]) -> dict[str, pa.DataType]:
    """Prepends a video_id field to a schema dict."""
    return {"video_id": pa.string(), **schema}

def chunk_video_ids(video_id_set: set[str], chunk_size: int = 50) -> list[list[str]]:
    """Chunks a set of video ids into lists of size chunk_size."""
    video_id_list = list(video_id_set)
    return [video_id_list[i:i + chunk_size] for i in range(0, len(video_id_list), chunk_size)]

def generate_date_ranges(start_date: str, end_date: str, days_delta: int = 7) -> list[tuple[str, str]]:
    """Generates a list of date ranges (start_date, end_date) for each month between start_year and end_year."""
    start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    delta = timedelta(days=days_delta)
    date_ranges = []
    while start_date <= end_date:
        date_ranges.append(start_date.isoformat())
        start_date += delta
    return list(zip(date_ranges[0:-1], date_ranges[1:]))

def _iso8601_duration_to_seconds(duration: str) -> int | None:
    """Converts an ISO 8601 duration string (e.g. 'PT1H2M3S') to total seconds."""
    if not duration:
        return None
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
    if not match:
        return None
    h, m, s = (int(x or 0) for x in match.groups())
    return h * 3600 + m * 60 + s

## bq functions

def load_data_to_partitioned_bq(table: pa.Table, table_name: str, dataset_name: str, job_schema: dict[str, pa.DataType], time_partition_field: str = "day") -> int:
    """Casts a pa.Table to the given schema and writes it to a time-partitioned BigQuery table.
    Infers partition dates from the time_partition_field column.
    Creates the table if it does not exist. Overwrites each partition present in the data.
    Returns the total number of rows loaded."""
    schema = pa.schema([(key, job_schema[key]) for key in job_schema])
    cast_table = table.cast(schema)
    base_table_id = f'{PROJECT_ID}.{dataset_name}.{table_name}'
    client = bigquery.Client(project=PROJECT_ID)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        time_partitioning=bigquery.table.TimePartitioning(field=time_partition_field),
    )
    unique_dates = sorted(cast_table.column(time_partition_field).unique().to_pylist(),reverse = True)
    total_rows = 0
    try:
        for partition_date in unique_dates:
            partition_table = cast_table.filter(
                pa.compute.equal(cast_table.column(time_partition_field), partition_date)
            )
            partition_str = partition_date.strftime("%Y%m%d") if hasattr(partition_date, "strftime") else str(partition_date).replace("-", "")
            destination_table_id = f'{base_table_id}${partition_str}'
            buf = io.BytesIO()
            pq.write_table(partition_table, buf)
            buf.seek(0)
            job = client.load_table_from_file(buf, destination_table_id, job_config=job_config)
            job.result()
            logger.info("Loaded %d rows to %s", partition_table.num_rows, destination_table_id)
            total_rows += partition_table.num_rows
        return total_rows
    except Exception:
        logger.exception("Failed to load to %s", base_table_id)
        raise

def load_data_to_bq(table: pa.Table, table_name: str, dataset_name: str, job_schema: dict[str, pa.DataType]) -> int:
    """Casts a pa.Table to the given schema and writes it to a non-partitioned BigQuery table.
    Creates the table if it does not exist. Overwrites existing data.
    Returns the number of rows loaded."""
    schema = pa.schema([(key, job_schema[key]) for key in job_schema])
    cast_table = table.cast(schema)
    destination_table_id = f'{PROJECT_ID}.{dataset_name}.{table_name}'
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    try:
        client = bigquery.Client(project=PROJECT_ID)
        buf = io.BytesIO()
        pq.write_table(cast_table, buf)
        buf.seek(0)
        job = client.load_table_from_file(buf, destination_table_id, job_config=job_config)
        job.result()
        logger.info("Loaded %d rows to %s", cast_table.num_rows, destination_table_id)
        return cast_table.num_rows
    except Exception:
        logger.exception("Failed to load to %s", destination_table_id)
        raise

def retrieve_data_from_bq(PROJECT_ID: str, dataset_name: str, table_name: str) -> pa.Table:
    """Reads all rows from a BigQuery table and returns them as a pa.Table."""
    destination_table_id = f'{PROJECT_ID}.{dataset_name}.{table_name}'
    query = f"SELECT * FROM `{destination_table_id}`"
    try:
        client = bigquery.Client(project=PROJECT_ID)
        return client.query(query).to_arrow()
    except Exception:
        logger.exception("Failed to retrieve data from %s", destination_table_id)
        raise

## generic query functions 

def _run_report(
    youtube_service: Resource,
    video_ids: list[str],
    start_date: str,
    end_date: str,
    bq_schema: dict,
    config_fn: Callable[[str], dict[str, Any]],
    label: str,
) -> pa.Table:
    """Fetches a daily report for all videos with timing and logging."""
    time_start = time.time()
    table = fetch_daily_report_for_all_videos(
        youtube_service, video_ids, start_date, end_date, bq_schema, config_fn,
    )
    logger.debug("%s for sample of videos:\n%s", label, table)
    logger.info("Time taken to retrieve %s for %d videos: %.2f seconds",
                label, len(table), time.time() - time_start)
    return table

def fetch_daily_report_for_all_videos(
    youtube_analytics_service: Resource,
    video_ids: list[str],
    start_date: str,
    end_date: str,
    bq_schema: dict,
    config_fn: Callable[[str], dict[str, Any]],
) -> pa.Table:
    """Fetches a daily analytics report for each video ID and concatenates the results into a single pa.Table."""
    from googleapiclient.errors import HttpError
    full_schema = _with_video_id(bq_schema)
    schema = pa.schema([(k, v) for k, v in full_schema.items()])
    tables = []
    failed_ids = []
    for id in video_ids:
        logger.info("Video ID from BQ: %s", id)
        try:
            table = generic_analytics_report(youtube_analytics_service, start_date=start_date, end_date=end_date, **config_fn(id))
        except HttpError as e:
            if e.resp.status == 429:
                logger.warning("Rate limit hit at video %s, pausing 60s before continuing", id)
                time.sleep(60)
                try:
                    table = generic_analytics_report(youtube_analytics_service, start_date=start_date, end_date=end_date, **config_fn(id))
                except Exception:
                    logger.warning("Failed to fetch report for video %s after rate limit retry, skipping", id, exc_info=True)
                    failed_ids.append(id)
                    continue
            else:
                logger.warning("Failed to fetch report for video %s, skipping", id, exc_info=True)
                failed_ids.append(id)
                continue
        except Exception:
            logger.warning("Failed to fetch report for video %s, skipping", id, exc_info=True)
            failed_ids.append(id)
            continue
        video_id_column = pa.array([id] * len(table), type=pa.string())
        table = table.add_column(0, "video_id", video_id_column)
        table = table.cast(schema)
        tables.append(table)
        time.sleep(0.1)
    if failed_ids:
        logger.warning("Failed to fetch report for %d videos: %s", len(failed_ids), failed_ids)
    return pa.concat_tables(tables) if tables else schema.empty_table()

def generic_analytics_report(youtube_service: Resource, start_date: str, end_date: str, video_id: str | None = None, max_retries: int = 3, **kwargs: Any) -> pa.Table:
    """Executes a YouTube Analytics API query with the given parameters and returns results as a pa.Table.
    Retries on transient HTTP 5xx errors with exponential backoff."""
    if video_id is not None:
        kwargs["filters"] = f"video=={video_id}"
    if start_date > end_date:
        raise ValueError(f"start_date ({start_date}) must be before or equal to end_date ({end_date})")
    query_params: dict[str, Any] = {"ids": "channel==MINE", "startDate": start_date, "endDate": end_date, **kwargs}
    from googleapiclient.errors import HttpError
    for attempt in range(max_retries):
        try:
            response = youtube_service.reports().query(
                **query_params
            ).execute()
            break
        except HttpError as e:
            if e.resp.status >= 500 and attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning("Transient API error (HTTP %d) for video %s, retrying in %ds (attempt %d/%d)",
                               e.resp.status, video_id or "N/A", wait, attempt + 1, max_retries)
                time.sleep(wait)
            else:
                raise

    headers = [col["name"] for col in response.get("columnHeaders", [])]
    rows = response.get("rows", [])
    columns = dict(zip(headers, zip(*rows))) if rows else {h: [] for h in headers}
    return pa.table(columns)


## main function

def main() -> None:
    """Entry point: discovers video IDs, fetches metadata, and runs daily analytics reports."""
    logging.basicConfig(level=logging.INFO)
    RUN_DAILY_REPORTS = True
    REFRESH_YOUTUBE_IDS_AND_METADATA = False  # set to True to refresh the list of video IDs (will create up to 200 new videos per date range chunk)
    SMALL_SAMPLE = False  # set to True to only run analytics report for a small sample of videos (will use hardcoded video IDs instead of retrieving from BQ)
    RUN_PERIODIC_REPORTS= False# perform the multi month demogrpahic, device and engagement analysis
    START_DATE = "2021-01-01"
    END_DATE = "2022-12-31"
    try:
        youtube_analytics_service = get_youtube_analytics_api_v2_service()
        youtube_data_service = get_youtube_data_api_v3_service()

        ## Step 1: Get video IDs for the channel in the date range using Analytics API v2
        if REFRESH_YOUTUBE_IDS_AND_METADATA:
            START_DATE = "2024-01-01"
            END_DATE = "2026-03-01"
            ### create_vido_ids using Analytics API v2 - could be replaced now we have Youtube Data API v3 access, but leaving as is for now to avoid hitting Data API quota limits during development
            youtube_id_table = get_top_200_videos_for_date_range(youtube_analytics_service, start_date=START_DATE, end_date=END_DATE)

            ### Youtube Data API v3 functions
            ## Given video_ids, get titles
            vido_ids_titles_table = get_video_titles(youtube_data_service ,youtube_id_table["video"].to_pylist())
            logger.info("Video ID titles:\n%s", vido_ids_titles_table)
            load_data_to_bq( vido_ids_titles_table ,"video_id_title_mapping","govuk_youtube_channel_staging",VIDEO_ID_BQ_SCHEMA)

            ## Given video_ids, get metatdata 
            youtube_ids_with_metadata_mapping_table = get_video_metadata(youtube_data_service, youtube_id_table["video"].to_pylist())
            logger.info("Video metadata:\n%s", youtube_ids_with_metadata_mapping_table)
            load_data_to_bq( youtube_ids_with_metadata_mapping_table ,"video_id_metadata_mapping","govuk_youtube_channel_staging",VIDEO_METADATA_BQ_SCHEMA)
        elif SMALL_SAMPLE:
        #small sample 
            youtube_ids_with_metadata_mapping_table  = pa.Table.from_arrays([['zbkxWchTyBU', 'ALvIYIC1IqE', 'AGFWRIN7WlI', 'O6UNpjQnphY', 'YaMjajmDbG0', 'FvtNyOVK4t8']],names =["video_id"])
        else:
        # full data from BQ
            logger.info("Skipping video ID and metadata retrieval. Set REFRESH_YOUTUBE_IDS_AND_METADATA to True to enable.")
            youtube_ids_with_metadata_mapping_table =retrieve_data_from_bq(PROJECT_ID, "govuk_youtube_channel_staging","video_id_metadata_mapping")
        
        ## Daily Reports
        video_ids = youtube_ids_with_metadata_mapping_table["video_id"].to_pylist()

        # ## Youtube Analytics API v2 services

        user_activity_table = _run_report(youtube_analytics_service, video_ids, START_DATE, END_DATE,
            USER_ACTIVITY_BQ_SCHEMA, get_user_activity_single_video_single_day_config, "User Activity")
        load_data_to_partitioned_bq(user_activity_table,'user_activity','govuk_youtube_channel_staging',_with_video_id(USER_ACTIVITY_BQ_SCHEMA), time_partition_field="day")

        playback_details_table = _run_report(youtube_analytics_service, video_ids, START_DATE, END_DATE,
            PLAYBACK_DETAILS_BQ_SCHEMA, get_playback_details_single_video_single_day_config, "Playback Details")
        load_data_to_partitioned_bq(playback_details_table,'playback_details','govuk_youtube_channel_staging',_with_video_id(PLAYBACK_DETAILS_BQ_SCHEMA), time_partition_field="day")

        playback_location_table = _run_report(youtube_analytics_service, video_ids, START_DATE, END_DATE,
            PLAYBACK_LOCATION_BQ_SCHEMA, get_playback_locations_single_video_single_day_config, "Playback Location")
        load_data_to_partitioned_bq(playback_location_table,'playback_location','govuk_youtube_channel_staging',_with_video_id(PLAYBACK_LOCATION_BQ_SCHEMA), time_partition_field="day")

        traffic_sources_table = _run_report(youtube_analytics_service, video_ids, START_DATE, END_DATE,
            TRAFFIC_SOURCES_BQ_SCHEMA, get_traffic_sources_single_video_single_day_config, "Traffic Sources")
        load_data_to_partitioned_bq(traffic_sources_table,'traffic_sources','govuk_youtube_channel_staging',_with_video_id(TRAFFIC_SOURCES_BQ_SCHEMA), time_partition_field="day")

        operating_system_and_device_table = _run_report(youtube_analytics_service, video_ids, START_DATE, END_DATE,
            OPERATING_SYSTEM_AND_DEVICE_TYPE_BQ_SCHEMA, get_operating_system_and_device_type_config, "Operating System and Device")
        load_data_to_partitioned_bq(operating_system_and_device_table,'operating_system_and_device','govuk_youtube_channel_staging',_with_video_id(OPERATING_SYSTEM_AND_DEVICE_TYPE_BQ_SCHEMA), time_partition_field="day")

        if RUN_PERIODIC_REPORTS:
            # START_DATE = "2025-01-01"
            # END_DATE = "2026-03-11"
            START_DATE = "2025-01-01"
            END_DATE = "2026-01-10"

            # operating_system_and_device_table = _run_report(youtube_analytics_service, video_ids, START_DATE, END_DATE,
            #     OPERATING_SYSTEM_AND_DEVICE_TYPE_BQ_SCHEMA, get_operating_system_and_device_type_config, "Operating System and Device")

            viewer_demographic_table = _run_report(youtube_analytics_service, video_ids, START_DATE, END_DATE,
                VIEWER_DEMOGRAPHICS_BQ_SCHEMA, get_viewer_demographics_config, "Viewer Demographics")


            # engagement_and_content_table = _run_report(youtube_analytics_service, video_ids, START_DATE, END_DATE,
            #     ENGAGEMENT_AND_CONTENT_SHARING_BQ_SCHEMA, get_engagement_and_content_sharing_config, "Engagement and Content Sharing")
            load_data_to_bq(viewer_demographic_table,'viewer_demographic','govuk_youtube_channel_staging',_with_video_id(VIEWER_DEMOGRAPHICS_BQ_SCHEMA))
            # load_data_to_bq(engagement_and_content_table,'engagement_and_content_location','govuk_youtube_channel_staging', _with_video_id(ENGAGEMENT_AND_CONTENT_SHARING_BQ_SCHEMA))


    except Exception:
        logger.exception("Pipeline failed")
        raise

if __name__ == "__main__":
    main()