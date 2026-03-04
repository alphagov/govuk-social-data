# -*- coding: utf-8 -*-

import json
import logging
import os
import re
from typing import Any
import googleapiclient.discovery
from googleapiclient.discovery import Resource
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.cloud import secretmanager
import pprint
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pyarrow as pa
import pyarrow.bigquery as pa_bq
from google.cloud import bigquery

PROJECT_ID = "gds-social-data"

CLIENT_ID_STRING_SECRET = "youtube-processing-client-id-web-app"
CLIENT_SECRET_STRING_SECRET = "youtube-processing-client-secret-web-app"
CHANNEL_ID = "UCSNK6abAoM6Kj0SkHOStNLg"
TOKEN_SECRET_VERSION= "5"  # 1 or "latest"
TOKEN_JSON_SECRET = "youtube-processing-web-client-token-json"
TOKEN_CACHE_PATH = os.path.expanduser("~/.config/youtube_exploration/token_cache.json")

# METRICS = ["engagedViews","views","estimatedMinutesWatched","likes","subscribersGained","subscribersLost","shares","comments","averageViewDuration","averageViewPercentage"]
METRICS= ["views"]
# DIMENSIONS = ["day", "gender","deviceType","video","ageGroup"]
DIMENSIONS = ["video"]

# by day 

user_activity_single_video_single_day_config = {
    "dimensions":"day,creatorContentType",
    "metrics":"engagedViews,views,redViews,comments,likes,dislikes,shares,estimatedMinutesWatched,estimatedRedMinutesWatched,averageViewDuration,averageViewPercentage,annotationClickThroughRate,annotationCloseRate,annotationImpressions,annotationClickableImpressions,annotationClosableImpressions,annotationClicks,annotationCloses,cardClickRate,cardTeaserClickRate,cardImpressions,cardTeaserImpressions,cardClicks,cardTeaserClicks",
    "maxResults":200,
    "sort":"-views"
}

playback_details_single_video_single_day_config = {
    "dimensions":"day",
    "metrics"   :"engagedViews, views, redViews, comments, likes, dislikes, videosAddedToPlaylists, videosRemovedFromPlaylists, shares, estimatedMinutesWatched, estimatedRedMinutesWatched, averageViewDuration, averageViewPercentage, annotationClickThroughRate, annotationCloseRate, annotationImpressions, annotationClickableImpressions, annotationClosableImpressions, annotationClicks, annotationCloses, cardClickRate, cardTeaserClickRate, cardImpressions, cardTeaserImpressions, cardClicks, cardTeaserClicks, videoThumbnailImpressions, videoThumbnailImpressionsClickRate, subscribersGained, subscribersLost, uniques",
    "maxResults":200,
    "sort":"-views"  

}

#placeholder
playback_locations_single_video_single_day = {
    "dimensions":"day",
    "metrics":"engagedViews,views,redViews,comments,likes,dislikes,shares,estimatedMinutesWatched,estimatedRedMinutesWatched,averageViewDuration,averageViewPercentage,annotationClickThroughRate,annotationCloseRate,annotationImpressions,annotationClickableImpressions,annotationClosableImpressions,annotationClicks,annotationCloses,cardClickRate,cardTeaserClickRate,cardImpressions,cardTeaserImpressions,cardClicks,cardTeaserClicks",
    "maxResults":200,
}


traffic_sources_single_video_single_day = {
}

traffic_sources_single_video_single_day = {
}

## not by day 
single_video_traffic_source_details={

}

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



def _save_token_cache(credentials: Credentials) -> None:
    os.makedirs(os.path.dirname(TOKEN_CACHE_PATH), exist_ok=True)
    with open(TOKEN_CACHE_PATH, "w") as f:
        json.dump({
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
        }, f)

def _load_token_cache(client_id: str, client_secret: str) -> Credentials | None:
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
        credentials.refresh(Request())
        _save_token_cache(credentials)
    return credentials


def get_gcp_secret(secret_name: str, secret_version: str = "latest", project_id: str = PROJECT_ID) -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/{secret_version}"
    payload = client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")
    return payload

def get_youtube_analytics_api_v2_service() -> Resource:
    client_id = get_gcp_secret(CLIENT_ID_STRING_SECRET)
    client_secret = get_gcp_secret(CLIENT_SECRET_STRING_SECRET)

    credentials = _load_token_cache(client_id, client_secret)
    if credentials is None:
        # Cold start: pull from Secret Manager.
        # expires_in in the stored token is relative to issuance time (unknown), so
        # force a refresh immediately to get a fresh access token with a real expiry.
        token_info = json.loads(get_gcp_secret(TOKEN_JSON_SECRET, TOKEN_SECRET_VERSION))
        refresh_token_expires_in = token_info.get("refresh_token_expires_in")
        if refresh_token_expires_in:
            print(f"Note: refresh token expires in {refresh_token_expires_in} seconds (~{refresh_token_expires_in // 86400} days)")
        credentials = Credentials(
            token=None,  # force refresh — stored access token may be stale
            refresh_token=token_info.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
        )
        credentials.refresh(Request())
        _save_token_cache(credentials)

    return googleapiclient.discovery.build("youtubeAnalytics", "v2", credentials=credentials)

def get_youtube_data_api_v3_service() -> Resource:
    client_id = get_gcp_secret(CLIENT_ID_STRING_SECRET)
    client_secret = get_gcp_secret(CLIENT_SECRET_STRING_SECRET)
    credentials = _load_token_cache(client_id, client_secret)
    if credentials is None:
        token_info = json.loads(get_gcp_secret(TOKEN_JSON_SECRET, TOKEN_SECRET_VERSION))
        credentials = Credentials(
            token=None,
            refresh_token=token_info.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        )
        credentials.refresh(Request())
        _save_token_cache(credentials)
    return googleapiclient.discovery.build("youtube", "v3", credentials=credentials)

def _iso8601_duration_to_seconds(duration: str) -> int | None:
    if not duration:
        return None
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
    if not match:
        return None
    h, m, s = (int(x or 0) for x in match.groups())
    return h * 3600 + m * 60 + s

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
        print(range_start, range_end)
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
            print(f"Warning: Retrieved {len(rows)} rows (max per request). Consider shortening the date range.")

    print(f"Found {len(video_ids)} unique videos")
    return pa.table({"video": list(video_ids)})


def get_user_activity_single_video_single_day(youtube_service: Resource, start_date: str, end_date: str, video_id: str) -> pa.Table:
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


def generic_analytics_report(youtube_service: Resource, start_date: str, end_date: str, video_id: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
    if video_id is not None:
        kwargs["filters"] = f"video=={video_id}"
    response = youtube_service.reports().query(
        ids         ="channel==MINE",
        startDate   = start_date,
        endDate     = end_date,
        **kwargs
    ).execute()

    headers = [col["name"] for col in response.get("columnHeaders", [])]
    rows = response.get("rows", [])
    return [dict(zip(headers, row)) for row in rows]



def create_table(table: list[list[Any]], headers: list[str] | None = None) -> None:
    if headers:
        headstring = "\t{}\t" * len(headers)
        print(headstring.format(*headers))
    
    rowstring = "\t{}\t" * len(table[0])
    for row in table: 
        print(rowstring.format(*row))


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

def chunk_video_ids(video_id_set: set[str], chunk_size: int = 50) -> list[list[str]]:
    """Chunks a set of video ids into lists of size chunk_size."""
    video_id_list = list(video_id_set)
    return [video_id_list[i:i + chunk_size] for i in range(0, len(video_id_list), chunk_size)]

def load_data_to_bq(table: pa.Table, table_name: str, dataset_name: str, job_schema: dict[str, pa.DataType], overwrite: bool = True) -> int:
        destination_table_id = f'{PROJECT_ID}.{dataset_name}.{table_name}'
        schema = pa.schema([(key, job_schema[key]) for key in job_schema])
        cast_table = table.cast(schema)
        try:
            pa_bq.write_table(cast_table, destination_table_id, table_overwrite=overwrite)
            print(f"Loaded {cast_table.num_rows} rows to {destination_table_id}")
            return cast_table.num_rows
        except Exception as e:
            print(f"Failed to load to {destination_table_id}: {e}")
            raise


def main() -> None:
    try:
        youtube_analytics_service = get_youtube_analytics_api_v2_service()
        youtube_data_service = get_youtube_data_api_v3_service()

        start_date = "2021-01-01"
        end_date = "2021-01-08"

        ### Youtube Analytics API v2 services
        ### create_vido_ids using Analytics API v2
        youtube_id_table = get_top_200_videos_for_date_range(youtube_analytics_service, start_date=start_date, end_date=end_date)

        ## Given video_id + date range, get user activity metrics by day
        # user_table = get_user_activity_single_video_single_day(youtube_analytics_service,"2021-01-01", "2021-01-08","1hbnPCdM4ls")
        # user_table  = generic_analytics_report(youtube_analytics_service,start_date, end_date,video_id="1hbnPCdM4ls", **user_activity_single_video_single_day_config)
        # print(user_table)



        ### Youtube Data API v3 functions
        ## Given video_ids, get titles
        vido_ids_titles_table = get_video_titles(youtube_data_service ,youtube_id_table["video"].to_pylist())
        print(vido_ids_titles_table)
        # load_data_to_bq( vido_ids_titles_table ,"video_id_title_mapping","govuk_youtube_channel_staging",VIDEO_ID_BQ_SCHEMA)

        ## Given video_ids, get metatdata 
        youtube_ids_with_metadata_mapping_table = get_video_metadata(youtube_data_service, youtube_id_table["video"].to_pylist())
        print(youtube_ids_with_metadata_mapping_table)
        # load_data_to_bq( youtube_ids_with_metadata_mapping_table ,"video_id_metadata_mapping","govuk_youtube_channel_staging",VIDEO_METADATA_BQ_SCHEMA)



    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()