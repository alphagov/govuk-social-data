# -*- coding: utf-8 -*-

import argparse
import io
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Callable

import requests
import googleapiclient.discovery
import pyarrow as pa
import pyarrow.parquet as pq
from google.auth import exceptions as auth_exceptions
from google.auth.transport.requests import Request
from google.cloud import bigquery
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource

from src.authentication import FileTokenCache, get_gcp_secret

logger = logging.getLogger(__name__)

PROJECT_ID = "gds-social-data"

CLIENT_ID_STRING_SECRET = "youtube-processing-client-id-web-app"
CLIENT_SECRET_STRING_SECRET = "youtube-processing-client-secret-web-app"
CHANNEL_ID = "UCSNK6abAoM6Kj0SkHOStNLg"
TOKEN_SECRET_VERSION = "10"  # token version or "latest"
TOKEN_JSON_SECRET = "youtube-processing-web-client-token-json"
_YOUTUBE_TOKEN_CACHE = FileTokenCache("~/.config/youtube_exploration/token_cache.json")


youtube_traffic_sources = {
    "ADVERTISING": (
        "The viewer was referred to the video by an advertisement."
        " The insightTrafficSourceDetail field identifies the type of advertisement."
    ),
    "ANNOTATION": "Viewers reached the video by clicking on an annotation in another video.",
    "CAMPAIGN_CARD": (
        "Views originated from claimed, user-uploaded videos used to promote content."
        " Supported for content owner reports only."
    ),
    "END_SCREEN": "The views were referred from the end screen of another video.",
    "EXT_URL": (
        "The video views were referred from a link on another website, including Google Search results."
        " The insightTrafficSourceDetail field identifies the web page."
    ),
    "HASHTAGS": "Views originated from VOD hashtag pages or Shorts hashtag pivot pages.",
    "LIVE_REDIRECT": "The video views were referred from Live Redirects.",
    "NO_LINK_EMBEDDED": "The video was embedded on another website when it was viewed.",
    "NO_LINK_OTHER": ("Direct traffic or traffic from mobile apps where YouTube did not identify a referrer."),
    "NOTIFICATION": "The video views were referred from an email or notification from YouTube.",
    "PLAYLIST": (
        "The video views occurred while the video was being played as part of a playlist" " or from the playlist page."
    ),
    "PRODUCT_PAGE": "The video views were referred from a Product page.",
    "PROMOTED": (
        "The video views were referred from an unpaid YouTube promotion," " such as the 'Spotlight Videos' page."
    ),
    "RELATED_VIDEO": (
        "The video views were referred from a related video listing."
        " The insightTrafficSourceDetail field specifies the referring video ID."
    ),
    "SHORTS": ("The viewer was referred by swiping vertically from the previous video" " in the Shorts player."),
    "SOUND_PAGE": "Views originated from Shorts sound pivot pages.",
    "SUBSCRIBER": (
        "Views referred from homepage feeds or subscription features." " Details specify the specific feed items."
    ),
    "YT_CHANNEL": (
        "The video views occurred on a channel page." " The insightTrafficSourceDetail field specifies the channel ID."
    ),
    "YT_OTHER_PAGE": "Views referred from links on YouTube pages other than search or related videos.",
    "YT_SEARCH": (
        "Views referred from YouTube search results."
        " The insightTrafficSourceDetail field specifies the search term used."
    ),
    "VIDEO_REMIXES": "Views originating from videos that used the content as a sample or remix.",
}


# Reports that can be over a wide date time, with day dimension


def get_user_activity_single_video_config(video_id: str) -> dict[str, Any]:
    """Returns the configuration for retrieving user activity metrics for a single video on a daily basis.
    This function has been replaced by generic_report_config but is useful for troubleshooting"""
    return {
        "dimensions": "day",
        "metrics": (
            "engagedViews,views,redViews,comments,likes,dislikes,shares,"
            "estimatedMinutesWatched,estimatedRedMinutesWatched,"
            "averageViewDuration,averageViewPercentage,"
            "annotationClickThroughRate,annotationCloseRate,"
            "annotationImpressions,annotationClickableImpressions,"
            "annotationClosableImpressions,annotationClicks,"
            "annotationCloses,cardClickRate,cardTeaserClickRate,"
            "cardImpressions,cardTeaserImpressions,cardClicks,"
            "cardTeaserClicks"
        ),
        "filters": f"video=={video_id}",
    }


def get_playback_details_single_video_config(video_id: str) -> dict[str, Any]:
    """Returns the configuration for retrieving playback details
    metrics for a single video on a daily basis.
    Note the creatorContentType, informs us if its a video,short or
    live stream, this is important as some metrics are only available
    for certain content types, e.g. engagedViews is not available
    for shorts.
    """
    return {
        "dimensions": "day,creatorContentType",
        "metrics": (
            "engagedViews,views,redViews,"
            "estimatedMinutesWatched,"
            "estimatedRedMinutesWatched,"
            "averageViewDuration,averageViewPercentage"
        ),
        "filters": f"video=={video_id}",
    }


def get_playback_locations_single_video_config(video_id: str) -> dict[str, Any]:
    """Returns the configuration for retrieving playback location
    metrics for a single video on a daily basis.
    Note that insightPlaybackLocationType dimension informs us where
    the video was played e.g. on YouTube watch page, embedded on
    another website, etc. This is important as some metrics are only
    available for certain playback locations, e.g. engagedViews is
    not available for embedded playback location type.
    https://developers.google.com/youtube/analytics/channel_reports
    """
    return {
        "dimensions": "day,insightPlaybackLocationType,creatorContentType",
        "metrics": "engagedViews,views,estimatedMinutesWatched",
        "filters": f"video=={video_id}",
    }


def get_traffic_sources_single_video_config(video_id: str) -> dict[str, Any]:
    """
    note that traffic sources requires insightTrafficSourcesTypes
    allows single day, traffic sources detials does not allow day
    dimension. We can not query more then 50,000 day*video records
    at a time. As we have 600 videos max, we should not query more
    then 90 days a time for traffic source. I assume Dimensions
    are not effected.
    """
    return {
        "dimensions": "day,insightTrafficSourceType,creatorContentType",
        "metrics": "engagedViews,views,estimatedMinutesWatched",
        "filters": f"video=={video_id}",
    }


def get_operating_system_and_device_type_single_video_config(video_id: str) -> dict[str, Any]:
    """Returns the configuration for retrieving OS and device type
    metrics for a single video on a daily basis."""
    return {
        "dimensions": "day,deviceType,operatingSystem",
        "metrics": "engagedViews,views,estimatedMinutesWatched",
        "filters": f"video=={video_id}",
    }


# Reports which do not allow day dimension
# note that traffic sources requires insightTrafficSourcesTypes
# allows single day, traffic sources detials does not allow day dimension
def get_viewer_demographics_config(video_id: str) -> dict[str, Any]:
    """Returns the configuration for retrieving viewer demographics (age group and gender) for a single video."""
    return {"dimensions": "ageGroup,gender", "metrics": "viewerPercentage", "filters": f"video=={video_id}"}


def get_engagement_and_content_sharing_config(video_id: str) -> dict[str, Any]:
    """Returns the configuration for retrieving content sharing metrics by sharing service for a single video."""
    return {"dimensions": "sharingService", "metrics": "shares", "filters": f"video=={video_id}"}


def get_user_activity_by_country_code_single_video_single_day_config(
    video_id: str, country_code: str
) -> dict[str, Any]:
    """Returns the configuration for retrieving user activity
    metrics for a single video on a daily basis.
    This function has been replaced by generic_report_config but
    is useful for troubleshooting"""
    return {
        "dimensions": "day",
        "metrics": (
            "engagedViews,views,redViews,comments,likes,dislikes,"
            "shares,estimatedMinutesWatched,"
            "estimatedRedMinutesWatched,"
            "averageViewDuration,averageViewPercentage,"
            "annotationClickThroughRate,annotationCloseRate,"
            "annotationImpressions,annotationClickableImpressions,"
            "annotationClosableImpressions,annotationClicks,"
            "annotationCloses,cardClickRate,cardTeaserClickRate,"
            "cardImpressions,cardTeaserImpressions,cardClicks,"
            "cardTeaserClicks"
        ),
        "filters": f"video=={video_id};country=={country_code}",
    }


# Reports not implemented
# playback location detail as it only shows information for
# insightPlaybackLocationType==Embedded and does not allow dim by day
# Traffic source detail does not allow dim by day
# audience retiontion (wht part of the videos they watched)


VIDEO_ID_BQ_SCHEMA = {
    "video_id": pa.string(),
    "video_title": pa.string(),
}

VIDEO_METADATA_BQ_SCHEMA = {
    "video_id": pa.string(),
    "title": pa.string(),
    "publishedAt": pa.string(),
    "description": pa.string(),
    "tags": pa.list_(pa.string()),
    "categoryId": pa.string(),
    "categoryTitle": pa.string(),
    "channelTitle": pa.string(),
    "channelId": pa.string(),
    "defaultLanguage": pa.string(),
    "defaultAudioLanguage": pa.string(),
    "duration": pa.string(),
    "duration_in_seconds": pa.int64(),
    "definition": pa.string(),
    "caption": pa.string(),
    "regionRestriction_allowed": pa.list_(pa.string()),
    "regionRestriction_blocked": pa.list_(pa.string()),
    "viewCount": pa.int64(),
    "likeCount": pa.int64(),
    "commentCount": pa.int64(),
    "favoriteCount": pa.int64(),
    "privacyStatus": pa.string(),
    "madeForKids": pa.bool_(),
    "embeddable": pa.bool_(),
}

USER_ACTIVITY_BQ_SCHEMA = {
    "day": pa.date32(),
    "engagedViews": pa.int64(),
    "views": pa.int64(),
    "redViews": pa.int64(),
    "comments": pa.int64(),
    "likes": pa.int64(),
    "dislikes": pa.int64(),
    "shares": pa.int64(),
    "estimatedMinutesWatched": pa.float64(),
    "estimatedRedMinutesWatched": pa.float64(),
    "averageViewDuration": pa.float64(),
    "averageViewPercentage": pa.float64(),
    "annotationClickThroughRate": pa.float64(),
    "annotationCloseRate": pa.float64(),
    "annotationImpressions": pa.int64(),
    "annotationClickableImpressions": pa.int64(),
    "annotationClosableImpressions": pa.int64(),
    "annotationClicks": pa.int64(),
    "annotationCloses": pa.int64(),
    "cardClickRate": pa.float64(),
    "cardTeaserClickRate": pa.float64(),
    "cardImpressions": pa.int64(),
    "cardTeaserImpressions": pa.int64(),
    "cardClicks": pa.int64(),
    "cardTeaserClicks": pa.int64(),
}

PLAYBACK_DETAILS_BQ_SCHEMA = {
    "day": pa.date32(),
    "creatorContentType": pa.string(),
    "engagedViews": pa.int64(),
    "views": pa.int64(),
    "redViews": pa.int64(),
    "estimatedMinutesWatched": pa.float64(),
    "estimatedRedMinutesWatched": pa.float64(),
    "averageViewDuration": pa.float64(),
    "averageViewPercentage": pa.float64(),
}

PLAYBACK_LOCATION_BQ_SCHEMA = {
    "day": pa.date32(),
    "insightPlaybackLocationType": pa.string(),
    "creatorContentType": pa.string(),
    "engagedViews": pa.int64(),
    "views": pa.int64(),
    "estimatedMinutesWatched": pa.float64(),
}

TRAFFIC_SOURCES_BQ_SCHEMA = {
    "day": pa.date32(),
    "insightTrafficSourceType": pa.string(),
    "creatorContentType": pa.string(),
    "engagedViews": pa.int64(),
    "views": pa.int64(),
    "estimatedMinutesWatched": pa.float64(),
}

OPERATING_SYSTEM_AND_DEVICE_TYPE_BQ_SCHEMA = {
    "day": pa.date32(),
    "deviceType": pa.string(),
    "operatingSystem": pa.string(),
    "engagedViews": pa.int64(),
    "views": pa.int64(),
    "estimatedMinutesWatched": pa.float64(),
}

VIEWER_DEMOGRAPHICS_BQ_SCHEMA = {
    "ageGroup": pa.string(),
    "gender": pa.string(),
    "viewerPercentage": pa.float64(),
}

ENGAGEMENT_AND_CONTENT_SHARING_BQ_SCHEMA = {
    "sharingService": pa.string(),
    "shares": pa.int64(),
}


USER_ACTIVITY_WITH_COUNTRY_CODE_BQ_SCHEMA = {
    "day": pa.date32(),
    "engagedViews": pa.int64(),
    "views": pa.int64(),
    "redViews": pa.int64(),
    "comments": pa.int64(),
    "likes": pa.int64(),
    "dislikes": pa.int64(),
    "shares": pa.int64(),
    "estimatedMinutesWatched": pa.float64(),
    "estimatedRedMinutesWatched": pa.float64(),
    "averageViewDuration": pa.float64(),
    "averageViewPercentage": pa.float64(),
    "annotationClickThroughRate": pa.float64(),
    "annotationCloseRate": pa.float64(),
    "annotationImpressions": pa.int64(),
    "annotationClickableImpressions": pa.int64(),
    "annotationClosableImpressions": pa.int64(),
    "annotationClicks": pa.int64(),
    "annotationCloses": pa.int64(),
    "cardClickRate": pa.float64(),
    "cardTeaserClickRate": pa.float64(),
    "cardImpressions": pa.int64(),
    "cardTeaserImpressions": pa.int64(),
    "cardClicks": pa.int64(),
    "cardTeaserClicks": pa.int64(),
}
# generated using GB region code + snippet https://developers.google.com/youtube/v3/docs/videoCategories/list
VIDEO_CATEGORY_ID_TO_TITLE = {
    "1": "Film & Animation",
    "2": "Autos & Vehicles",
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

# functions for single purpose reports


def get_video_metadata(youtube_dataapi_v3_service: Resource, video_ids: list[str]) -> pa.Table:
    """Fetches comprehensive metadata for a list of video IDs using the YouTube Data API v3.
    Returns a pa.Table with snippet, contentDetails, statistics, and status fields.
    The Data API accepts at most 50 IDs per request.
    """
    cols = {key: [] for key in VIDEO_METADATA_BQ_SCHEMA}
    for chunk in chunk_video_ids(set(video_ids), chunk_size=50):
        response = (
            youtube_dataapi_v3_service.videos()
            .list(
                part="snippet,contentDetails,statistics,status",
                id=",".join(chunk),
            )
            .execute()
        )
        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            content = item.get("contentDetails", {})
            stats = item.get("statistics", {})
            status = item.get("status", {})
            region = content.get("regionRestriction", {})

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


def get_all_videos_for_date_range(
    youtube_data_service: Resource,
) -> pa.Table:
    """Returns all video IDs for the channel by paginating through the uploads playlist
    using the YouTube Data API v3.
    """
    token = youtube_data_service._http.credentials.token
    playlist_id = f"UU{CHANNEL_ID[2:]}"
    video_ids = []
    params = {"part": "contentDetails", "maxResults": 50, "playlistId": playlist_id}

    while True:
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        video_ids.extend(item["contentDetails"]["videoId"] for item in data.get("items", []))
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break
        params["pageToken"] = next_page_token

    logger.info("Found %d unique videos", len(video_ids))
    return pa.table({"video": video_ids})


def get_user_activity_single_video_single_day(
    youtube_service: Resource, start_date: str, end_date: str, video_id: str
) -> pa.Table:
    """Queries daily user activity metrics for a single video and returns the results as a pa.Table."""
    response = (
        youtube_service.reports()
        .query(
            dimensions="day",
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            filters=f"video=={video_id}",
            metrics=(
                "engagedViews,views,redViews,comments,likes,"
                "dislikes,shares,estimatedMinutesWatched,"
                "estimatedRedMinutesWatched,averageViewDuration,"
                "averageViewPercentage,annotationClickThroughRate,"
                "annotationCloseRate,annotationImpressions,"
                "annotationClickableImpressions,"
                "annotationClosableImpressions,annotationClicks,"
                "annotationCloses,cardClickRate,"
                "cardTeaserClickRate,cardImpressions,"
                "cardTeaserImpressions,cardClicks,cardTeaserClicks"
            ),
        )
        .execute()
    )

    headers = [col["name"] for col in response.get("columnHeaders", [])]
    rows = response.get("rows", [])
    columns = dict(zip(headers, zip(*rows))) if rows else {h: [] for h in headers}
    return pa.table(columns)


def get_traffic_source_data_periodic(
    youtube_service: Resource, start_date: str, end_date: str, video_id: str
) -> pa.Table:
    """Queries daily user activity metrics for a single video and returns the results as a pa.Table."""
    response = (
        youtube_service.reports()
        .query(
            dimensions="day",
            ids="channel==MINE",
            filters=f"video=={video_id}",
            metrics=(
                "engagedViews,views,redViews,comments,likes,"
                "dislikes,shares,estimatedMinutesWatched,"
                "estimatedRedMinutesWatched,averageViewDuration,"
                "averageViewPercentage,annotationClickThroughRate,"
                "annotationCloseRate,annotationImpressions,"
                "annotationClickableImpressions,"
                "annotationClosableImpressions,annotationClicks,"
                "annotationCloses,cardClickRate,"
                "cardTeaserClickRate,cardImpressions,"
                "cardTeaserImpressions,cardClicks,cardTeaserClicks"
            ),
        )
        .execute()
    )

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
        response = (
            youtube_dataapi_v3_service.videos()
            .list(
                part="snippet",
                id=",".join(chunk),
            )
            .execute()
        )
        for item in response.get("items", []):
            video_id_col.append(item["id"])
            video_title_col.append(item["snippet"]["title"])
    return pa.table({"video_id": video_id_col, "video_title": video_title_col})


# authentication functions


def _save_token_cache(credentials: Credentials) -> None:
    """Persists OAuth2 credentials to the file token cache."""
    _YOUTUBE_TOKEN_CACHE.save(
        {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "expires_at": credentials.expiry.isoformat() if credentials.expiry else None,
        }
    )


def _load_token_cache(client_id: str, client_secret: str) -> Credentials | None:
    """Loads OAuth2 credentials from the file token cache, refreshing if expired.
    Returns None if no cache file exists."""
    data = _YOUTUBE_TOKEN_CACHE.load()
    if data is None:
        return None
    expiry = datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None
    credentials = Credentials(
        token=data.get("access_token"),
        refresh_token=data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[
            "https://www.googleapis.com/auth/yt-analytics.readonly",
            "https://www.googleapis.com/auth/youtube.readonly",
        ],
        expiry=expiry,
    )
    if not credentials.valid:
        if not credentials.refresh_token:
            return None
        try:
            credentials.refresh(Request())
            logger.info("Token scopes: %s", credentials.scopes)
            logger.info("Granted scopes: %s", credentials.granted_scopes)
        except Exception:
            logger.warning("Failed to refresh cached token, will re-fetch from Secret Manager")
            return None
        _save_token_cache(credentials)
    return credentials


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
    return _build_service(
        "youtubeAnalytics",
        "v2",
        ["https://www.googleapis.com/auth/yt-analytics.readonly", "https://www.googleapis.com/auth/youtube.readonly"],
    )


def get_youtube_data_api_v3_service() -> Resource:
    """Builds and returns an authenticated YouTube Data API v3 service client."""
    return _build_service("youtube", "v3", ["https://www.googleapis.com/auth/youtube.readonly"])


# helper functions


def _with_video_id(schema: dict[str, pa.DataType]) -> dict[str, pa.DataType]:
    """Prepends a video_id field to a schema dict."""
    return {"video_id": pa.string(), **schema}


def _with_country_code(schema: dict[str, pa.DataType]) -> dict[str, pa.DataType]:
    """Prepends a country_code field to a schema dict."""
    return {"country_code": pa.string(), **schema}


def _with_month(schema: dict[str, pa.DataType]) -> dict[str, pa.DataType]:
    """Prepends a month field to a schema dict."""
    return {"month": pa.date32(), **schema}


def chunk_video_ids(video_id_set: set[str], chunk_size: int = 50) -> list[list[str]]:
    """Chunks a set of video ids into lists of size chunk_size."""
    video_id_list = list(video_id_set)
    return [video_id_list[i : i + chunk_size] for i in range(0, len(video_id_list), chunk_size)]


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


def last_day_of_month(any_day):
    # The day 28 exists in every month. 4 days later, it's always next month
    next_month = any_day.replace(day=28) + timedelta(days=4)
    # subtracting the number of the current day brings us back one month
    return next_month - timedelta(days=next_month.day)


def generate_monthly_date_ranges(start_date: str, end_date: str) -> list[tuple[str, str]]:
    """Generates a list of (first_day, last_day) tuples for each full calendar month in the range."""
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    date_ranges = []
    current = start.replace(day=1)
    while current <= end:
        month_end = last_day_of_month(current)
        date_ranges.append((current.isoformat(), month_end.isoformat()))
        current = month_end + timedelta(days=1)
    return date_ranges


def _iso8601_duration_to_seconds(duration: str) -> int | None:
    """Converts an ISO 8601 duration string (e.g. 'PT1H2M3S') to total seconds."""
    if not duration:
        return None
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return None
    h, m, s = (int(x or 0) for x in match.groups())
    return h * 3600 + m * 60 + s


# bq functions


def load_data_to_partitioned_bq(
    table: pa.Table,
    table_name: str,
    dataset_name: str,
    job_schema: dict[str, pa.DataType],
    time_partition_field: str = "day",
) -> int:
    """Casts a pa.Table to the given schema and writes it to a time-partitioned BigQuery table.
    Infers partition dates from the time_partition_field column.
    Creates the table if it does not exist. Overwrites each partition present in the data.
    Returns the total number of rows loaded."""
    schema = pa.schema([(key, job_schema[key]) for key in job_schema])
    cast_table = table.cast(schema)
    base_table_id = f"{PROJECT_ID}.{dataset_name}.{table_name}"
    client = bigquery.Client(project=PROJECT_ID)
    if time_partition_field == "month":
        partition_type = bigquery.TimePartitioningType.MONTH
    else:
        partition_type = bigquery.TimePartitioningType.DAY
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        time_partitioning=bigquery.table.TimePartitioning(type_=partition_type, field=time_partition_field),
    )
    unique_dates = sorted(cast_table.column(time_partition_field).unique().to_pylist(), reverse=True)
    total_rows = 0
    try:
        for partition_date in unique_dates:
            partition_table = cast_table.filter(
                pa.compute.equal(cast_table.column(time_partition_field), partition_date)
            )
            if time_partition_field == "month":
                # date(2025, 1, 1) -> "202501"
                partition_str = partition_date.strftime("%Y%m")
            elif hasattr(partition_date, "strftime"):
                partition_str = partition_date.strftime("%Y%m%d")
            else:
                partition_str = str(partition_date).replace("-", "")
            destination_table_id = f"{base_table_id}${partition_str}"
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


def load_data_to_partitioned_bq_merge(
    table: pa.Table,
    table_name: str,
    dataset_name: str,
    job_schema: dict[str, pa.DataType],
    merge_keys: list[str],
    time_partition_field: str = "day",
) -> int:
    """Loads data to a partitioned BigQuery table using a staging table with MERGE
    to upsert rows without overwriting existing partition data.

    Data is first loaded into a staging table, then a MERGE statement matches on
    merge_keys to update existing rows and insert new ones.

    Args:
        table: The PyArrow table to load.
        table_name: The target BigQuery table name.
        dataset_name: The BigQuery dataset name.
        job_schema: Column name to PyArrow type mapping for schema casting.
        merge_keys: Columns that define row uniqueness for deduplication.
        time_partition_field: The column used for time partitioning.

    Returns:
        The total number of rows loaded.
    """
    schema = pa.schema([(key, job_schema[key]) for key in job_schema])
    cast_table = table.cast(schema)
    target_table_id = f"{PROJECT_ID}.{dataset_name}.{table_name}"
    staging_table_id = f"{PROJECT_ID}.{dataset_name}.{table_name}_staging"
    client = bigquery.Client(project=PROJECT_ID)

    # Step 1: Load all data into the staging table (WRITE_TRUNCATE is safe here)
    staging_job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        time_partitioning=bigquery.table.TimePartitioning(field=time_partition_field),
    )
    buf = io.BytesIO()
    pq.write_table(cast_table, buf)
    buf.seek(0)
    job = client.load_table_from_file(buf, staging_table_id, job_config=staging_job_config)
    job.result()
    logger.info("Loaded %d rows to staging table %s", cast_table.num_rows, staging_table_id)

    # Step 2: Create target table if it doesn't exist (using staging table schema)
    try:
        client.get_table(target_table_id)
    except Exception:
        staging_table_ref = client.get_table(staging_table_id)
        target_table = bigquery.Table(target_table_id, schema=staging_table_ref.schema)
        target_table.time_partitioning = bigquery.table.TimePartitioning(field=time_partition_field)
        client.create_table(target_table)
        logger.info("Created target table %s", target_table_id)

    # Step 3: MERGE from staging into target
    columns = list(job_schema.keys())
    on_clause = " AND ".join(f"T.{k} = S.{k}" for k in merge_keys)
    update_cols = [c for c in columns if c not in merge_keys]
    update_set = ", ".join(f"T.{c} = S.{c}" for c in update_cols)
    insert_columns = ", ".join(columns)
    insert_values = ", ".join(f"S.{c}" for c in columns)

    merge_sql = f"""
    MERGE `{target_table_id}` T
    USING `{staging_table_id}` S
    ON {on_clause}
    WHEN MATCHED THEN
      UPDATE SET {update_set}
    WHEN NOT MATCHED THEN
      INSERT ({insert_columns})
      VALUES ({insert_values})
    """

    try:
        client.query(merge_sql).result()
        logger.info("Merged %d rows into %s", cast_table.num_rows, target_table_id)
        return cast_table.num_rows
    except Exception:
        logger.exception("Failed to merge into %s", target_table_id)
        raise
    finally:
        client.delete_table(staging_table_id, not_found_ok=True)
        logger.info("Cleaned up staging table %s", staging_table_id)


def load_data_to_bq(table: pa.Table, table_name: str, dataset_name: str, job_schema: dict[str, pa.DataType]) -> int:
    """Casts a pa.Table to the given schema and writes it to a non-partitioned BigQuery table.
    Creates the table if it does not exist. Overwrites existing data.
    Returns the number of rows loaded."""
    schema = pa.schema([(key, job_schema[key]) for key in job_schema])
    cast_table = table.cast(schema)
    destination_table_id = f"{PROJECT_ID}.{dataset_name}.{table_name}"
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
    destination_table_id = f"{PROJECT_ID}.{dataset_name}.{table_name}"
    query = f"SELECT * FROM `{destination_table_id}`"
    try:
        client = bigquery.Client(project=PROJECT_ID)
        return client.query(query).to_arrow()
    except Exception:
        logger.exception("Failed to retrieve data from %s", destination_table_id)
        raise


# generic query functions


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
        youtube_service,
        video_ids,
        start_date,
        end_date,
        bq_schema,
        config_fn,
    )
    logger.debug("%s for sample of videos:\n%s", label, table)
    logger.info("Time taken to retrieve %s for %d videos: %.2f seconds", label, len(table), time.time() - time_start)
    return table


def _run_country_report(
    video_ids: list[str],
    start_date: str,
    end_date: str,
    country_codes: list[str],
    dataset_name: str,
    table_name: str = "user_activity_with_country",
    num_workers: int = 4,
) -> int:
    """Fetches user activity reports per country code and loads each to BigQuery via MERGE.
    Splits country_codes across multiple threads for parallel processing.
    Returns the total number of rows loaded."""

    def _process_country_chunk(chunk: list[str]) -> int:
        youtube_service = get_youtube_analytics_api_v2_service()
        full_country_schema = _with_country_code(_with_video_id(USER_ACTIVITY_WITH_COUNTRY_CODE_BQ_SCHEMA))
        country_schema = pa.schema([(k, v) for k, v in full_country_schema.items()])
        chunk_rows = 0
        for country_code in chunk:
            config_fn = partial(
                get_user_activity_by_country_code_single_video_single_day_config, country_code=country_code
            )
            table = _run_report(
                youtube_service,
                video_ids,
                start_date,
                end_date,
                USER_ACTIVITY_WITH_COUNTRY_CODE_BQ_SCHEMA,
                config_fn,
                f"User Activity - {country_code}",
            )
            if len(table) > 0:
                country_code_column = pa.array([country_code] * len(table), type=pa.string())
                table = table.add_column(0, "country_code", country_code_column)
                table = table.cast(country_schema)
                rows_loaded = load_data_to_partitioned_bq_merge(
                    table,
                    table_name,
                    dataset_name,
                    full_country_schema,
                    merge_keys=["country_code", "video_id", "day"],
                    time_partition_field="day",
                )
                chunk_rows += rows_loaded
        return chunk_rows

    country_code_chunks = [country_codes[i::num_workers] for i in range(num_workers)]

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(_process_country_chunk, chunk): i for i, chunk in enumerate(country_code_chunks) if chunk
        }
        total_rows = 0
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                rows = future.result()
                total_rows += rows
                logger.info("Worker %d finished, loaded %d rows", worker_id, rows)
            except Exception:
                logger.error("Worker %d failed", worker_id, exc_info=True)

    return total_rows


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
        logger.info("Running %s report for Video ID from BQ: %s", config_fn, id)
        try:
            table = generic_analytics_report(
                youtube_analytics_service, start_date=start_date, end_date=end_date, **config_fn(id)
            )
        except HttpError as e:
            if e.resp.status == 429:
                logger.warning("Rate limit hit at video %s, pausing 60s before continuing", id)
                time.sleep(60)
                try:
                    table = generic_analytics_report(
                        youtube_analytics_service, start_date=start_date, end_date=end_date, **config_fn(id)
                    )
                except Exception:
                    logger.warning(
                        "Failed to fetch %s report for video %s after rate limit retry, skipping",
                        config_fn,
                        id,
                        exc_info=True,
                    )
                    failed_ids.append(id)
                    continue
            if e.resp.status == 400:
                logger.warning(
                    "Incorrect Query found for report with schema %s for video id %s, check the query is supported",
                    config_fn,
                    id,
                    exc_info=True,
                )
                logger.error("Ending report")
                failed_ids.append(id)
                break
            else:
                logger.warning("Failed to fetch %s report for video %s, skipping", config_fn, id, exc_info=True)
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
        # time.sleep(0.2)
    if failed_ids:
        logger.warning("Failed to fetch report for %d videos: %s", len(failed_ids), failed_ids)
    return pa.concat_tables(tables) if tables else schema.empty_table()


def generic_analytics_report(
    youtube_service: Resource, start_date: str, end_date: str, max_retries: int = 3, **kwargs: Any
) -> pa.Table:
    """Executes a YouTube Analytics API query with the given parameters and returns results as a pa.Table.
    Filters (including video and country) should be provided via kwargs from the config function.
    Retries on transient HTTP 5xx errors with exponential backoff."""
    if start_date > end_date:
        raise ValueError(f"start_date ({start_date}) must be before or equal to end_date ({end_date})")

    query_params: dict[str, Any] = {"ids": "channel==MINE", "startDate": start_date, "endDate": end_date, **kwargs}
    from googleapiclient.errors import HttpError

    for attempt in range(max_retries):
        try:
            response = youtube_service.reports().query(**query_params).execute()
            break
        except HttpError as e:
            if e.resp.status >= 500 and attempt < max_retries - 1:
                wait = 2**attempt
                logger.warning(
                    "Transient API error (HTTP %d) for filters %s, retrying in %ds (attempt %d/%d)",
                    e.resp.status,
                    kwargs.get("filters", "N/A"),
                    wait,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait)
            else:
                raise
        except auth_exceptions.TransportError as e:
            if attempt < max_retries - 1:
                wait = 2**attempt
                logger.warning(
                    "Network/auth transport error for filters %s, retrying in %ds (attempt %d/%d): %s",
                    kwargs.get("filters", "N/A"),
                    wait,
                    attempt + 1,
                    max_retries,
                    e,
                )
                time.sleep(wait)
            else:
                raise

    headers = [col["name"] for col in response.get("columnHeaders", [])]
    rows = response.get("rows", [])
    columns = dict(zip(headers, zip(*rows))) if rows else {h: [] for h in headers}
    return pa.table(columns)


# main function


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for pipeline configuration."""
    parser = argparse.ArgumentParser(description="YouTube Analytics data ingestion pipeline")
    parser.add_argument(
        "--run-daily-reports",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run daily analytics reports per video (default: True)",
    )
    parser.add_argument(
        "--refresh-youtube-ids-and-metadata",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Refresh the list of video IDs and metadata (default: False)",
    )
    parser.add_argument(
        "--full-dataset",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use full dataset or a small hardcoded sample of video IDs (default: True)",
    )
    parser.add_argument(
        "--run-periodic-reports",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Run multi-month demographic, device and engagement analysis (default: False)",
    )
    parser.add_argument(
        "--start-date", default="2025-01-08", help="Start date for queries in YYYY-MM-DD format (default: 2025-01-08)"
    )
    parser.add_argument(
        "--end-date", default="2025-01-08", help="End date for queries in YYYY-MM-DD format (default: 2025-01-08)"
    )
    return parser.parse_args()


def main() -> None:
    """Entry point: discovers video IDs, fetches metadata, and runs daily analytics reports."""
    logging.basicConfig(level=logging.INFO)
    args = parse_args()
    RUN_DAILY_REPORTS = args.run_daily_reports
    REFRESH_YOUTUBE_IDS_AND_METADATA = args.refresh_youtube_ids_and_metadata
    FULL_DATASET = args.full_dataset
    RUN_PERIODIC_REPORTS = args.run_periodic_reports
    START_DATE = args.start_date
    END_DATE = args.end_date

    TEST_DATASET = "govuk_youtube_channel_testing"
    STAGING_DATASSET = "govuk_youtube_channel_staging"
    TARGET_DATASET = STAGING_DATASSET

    try:
        youtube_analytics_service = get_youtube_analytics_api_v2_service()
        youtube_data_service = get_youtube_data_api_v3_service()

        country_codes: list[str] = []
        set_of_country_codes = retrieve_data_from_bq(
            PROJECT_ID, "govuk_youtube_channel_staging", "country_code_to_country_mapping"
        )

        # Step 1: Get video IDs for the channel in the date range using Analytics API v2
        if REFRESH_YOUTUBE_IDS_AND_METADATA:
            # create_vido_ids using Analytics API v2 - could be replaced
            # now we have Youtube Data API v3 access, but leaving as is
            # for now to avoid hitting Data API quota limits during development
            youtube_id_table = get_all_videos_for_date_range(youtube_data_service)

            # Youtube Data API v3 functions
            # Given video_ids, get titles
            logger.info("Granted scopes: %s", youtube_data_service._http.credentials.scopes)
            logger.info("Has granted scopes: %s", youtube_data_service._http.credentials.granted_scopes)

            vido_ids_titles_table = get_video_titles(youtube_data_service, youtube_id_table["video"].to_pylist())
            logger.debug("Video ID titles:\n%s", vido_ids_titles_table)
            load_data_to_bq(vido_ids_titles_table, "video_id_title_mapping", TARGET_DATASET, VIDEO_ID_BQ_SCHEMA)

            # Given video_ids, get metatdata  s
            youtube_ids_with_metadata_mapping_table = get_video_metadata(
                youtube_data_service, youtube_id_table["video"].to_pylist()
            )
            logger.debug("Video metadata:\n%s", youtube_ids_with_metadata_mapping_table)
            load_data_to_bq(
                youtube_ids_with_metadata_mapping_table,
                "video_id_metadata_mapping",
                TARGET_DATASET,
                VIDEO_METADATA_BQ_SCHEMA,
            )

        if FULL_DATASET:
            youtube_ids_with_metadata_mapping_table = retrieve_data_from_bq(
                PROJECT_ID, TARGET_DATASET, "video_id_metadata_mapping"
            )
            country_codes = set_of_country_codes["country_code"].to_pylist()
            TARGET_DATASET = STAGING_DATASSET
        else:
            # full data from BQ
            logger.info(
                "Skipping video ID and metadata retrieval. Set REFRESH_YOUTUBE_IDS_AND_METADATA to True to enable."
            )
            # small sample
            youtube_ids_with_metadata_mapping_table = pa.Table.from_arrays(
                [
                    [
                        "4_1gld1PIkA",
                        "ucjmWjJ25Ho",
                        "USo1yHS5th0",
                        "GY-NpWFyu8w",
                        "xUZZKi94p6Q",
                        "RqZ9e1GXJLo",
                        "hs8sykCaf3E",
                        "I5gP62TX8aQ",
                        "1whaQq44Pew",
                        "oSZPWrZDINQ",
                    ]
                ],
                names=["video_id"],
            )
            country_codes = ["GB", "US"]
            TARGET_DATASET = TEST_DATASET

        # Daily Reports
        video_ids = youtube_ids_with_metadata_mapping_table["video_id"].to_pylist()

        if RUN_DAILY_REPORTS:

            # Youtube Analytics API v2 services
            # This must be run per day due to being unable to filter by both country and date
            user_activity_table = _run_report(
                youtube_analytics_service,
                video_ids,
                START_DATE,
                END_DATE,
                USER_ACTIVITY_BQ_SCHEMA,
                get_user_activity_single_video_config,
                "User Activity",
            )
            load_data_to_partitioned_bq(
                user_activity_table,
                "user_activity",
                TARGET_DATASET,
                _with_video_id(USER_ACTIVITY_BQ_SCHEMA),
                time_partition_field="day",
            )

            _run_country_report(
                video_ids,
                START_DATE,
                END_DATE,
                country_codes,
                TARGET_DATASET,
            )

            playback_location_table = _run_report(
                youtube_analytics_service,
                video_ids,
                START_DATE,
                END_DATE,
                PLAYBACK_LOCATION_BQ_SCHEMA,
                get_playback_locations_single_video_config,
                "Playback Location",
            )
            load_data_to_partitioned_bq(
                playback_location_table,
                "playback_location",
                TARGET_DATASET,
                _with_video_id(PLAYBACK_LOCATION_BQ_SCHEMA),
                time_partition_field="day",
            )

            traffic_sources_table = _run_report(
                youtube_analytics_service,
                video_ids,
                START_DATE,
                END_DATE,
                TRAFFIC_SOURCES_BQ_SCHEMA,
                get_traffic_sources_single_video_config,
                "Traffic Sources",
            )
            load_data_to_partitioned_bq(
                traffic_sources_table,
                "traffic_sources",
                TARGET_DATASET,
                _with_video_id(TRAFFIC_SOURCES_BQ_SCHEMA),
                time_partition_field="day",
            )

            operating_system_and_device_table = _run_report(
                youtube_analytics_service,
                video_ids,
                START_DATE,
                END_DATE,
                OPERATING_SYSTEM_AND_DEVICE_TYPE_BQ_SCHEMA,
                get_operating_system_and_device_type_single_video_config,
                "Operating System and Device",
            )
            load_data_to_partitioned_bq(
                operating_system_and_device_table,
                "operating_system_and_device",
                TARGET_DATASET,
                _with_video_id(OPERATING_SYSTEM_AND_DEVICE_TYPE_BQ_SCHEMA),
                time_partition_field="day",
            )

        if RUN_PERIODIC_REPORTS:
            monthly_ranges = generate_monthly_date_ranges(START_DATE, END_DATE)
            for month_start, month_end in monthly_ranges:
                month_str = month_start[:7]  # "YYYY-MM"
                month_date = datetime.strptime(month_start, "%Y-%m-%d").date()
                logger.info("Running periodic reports for month %s (%s to %s)", month_str, month_start, month_end)

                viewer_demographic_table = _run_report(
                    youtube_analytics_service,
                    video_ids,
                    month_start,
                    month_end,
                    VIEWER_DEMOGRAPHICS_BQ_SCHEMA,
                    get_viewer_demographics_config,
                    f"Viewer Demographics ({month_str})",
                )
                viewer_demographic_table = viewer_demographic_table.add_column(
                    0, "month", pa.array([month_date] * len(viewer_demographic_table), type=pa.date32())
                )
                load_data_to_partitioned_bq(
                    viewer_demographic_table,
                    "viewer_demographic",
                    TARGET_DATASET,
                    _with_month(_with_video_id(VIEWER_DEMOGRAPHICS_BQ_SCHEMA)),
                    time_partition_field="month",
                )

                engagement_and_content_table = _run_report(
                    youtube_analytics_service,
                    video_ids,
                    month_start,
                    month_end,
                    ENGAGEMENT_AND_CONTENT_SHARING_BQ_SCHEMA,
                    get_engagement_and_content_sharing_config,
                    f"Engagement and Content Sharing ({month_str})",
                )
                engagement_and_content_table = engagement_and_content_table.add_column(
                    0, "month", pa.array([month_date] * len(engagement_and_content_table), type=pa.date32())
                )
                load_data_to_partitioned_bq(
                    engagement_and_content_table,
                    "engagement_and_content_location",
                    TARGET_DATASET,
                    _with_month(_with_video_id(ENGAGEMENT_AND_CONTENT_SHARING_BQ_SCHEMA)),
                    time_partition_field="month",
                )

    except Exception:
        logger.exception("Pipeline failed")
        raise


if __name__ == "__main__":
    main()
