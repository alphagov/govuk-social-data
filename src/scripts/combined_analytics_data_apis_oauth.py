# -*- coding: utf-8 -*-

import json

import googleapiclient.discovery
from google.cloud import secretmanager
from google.oauth2.credentials import Credentials

PROJECT_ID = "1031179907628"
TOKEN_JSON_SECRET = "youtube-processing-web-client-token-json"
CLIENT_ID_STRING_SECRET = "youtube-processing-client-id-web-app"
CLIENT_SECRET_STRING_SECRET = "youtube-processing-client-secret-web-app"
CHANNEL_ID = "UCSNK6abAoM6Kj0SkHOStNLg"

# METRICS = ["engagedViews","views","estimatedMinutesWatched",
#     "likes","subscribersGained","subscribersLost","shares",
#     "comments","averageViewDuration","averageViewPercentage"]
METRICS = ["views"]
# DIMENSIONS = ["day", "gender","deviceType","video","ageGroup"]
DIMENSIONS = ["video"]


def get_gcp_secret(secret_name, project_id=PROJECT_ID):
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    payload = client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")
    return payload


def get_authenticated_service(API_NAME="youtubeAnalytics", API_VERSION="v2"):
    # Build the Credentials object which refreshest itself using the client_id/secret
    token_info = json.loads(get_gcp_secret(TOKEN_JSON_SECRET))
    client_id = get_gcp_secret(CLIENT_ID_STRING_SECRET)
    client_secret = get_gcp_secret(CLIENT_SECRET_STRING_SECRET)

    # Build the Credentials object which refreshest itself using the client_id/secret
    credentials = Credentials(
        token=token_info.get("access_token"),
        refresh_token=token_info.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
    )

    return googleapiclient.discovery.build(API_NAME, API_VERSION, credentials=credentials)


def get_all_video_stats(youtube_analytics, start_date, end_date):
    all_rows = []
    start_index = 1
    max_results = 200

    while True:
        response = (
            youtube_analytics.reports()
            .query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="views",
                dimensions="video",
                maxResults=max_results,
                startIndex=start_index,
                sort="-views",
            )
            .execute()
        )

        rows = response.get("rows", [])
        all_rows.extend(rows)

        # If we got fewer rows than the max, we've reached the end
        if len(rows) < max_results:
            break

        start_index += max_results

    return all_rows


def get_all_video_ids(youtube_data_service):
    # Step 1: get the uploads playlist ID for the channel
    channel_response = youtube_data_service.channels().list(part="contentDetails", mine=True).execute()
    uploads_playlist_id = channel_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # Step 2: paginate through the playlist
    video_ids = []
    next_page_token = None

    while True:
        playlist_response = (
            youtube_data_service.playlistItems()
            .list(part="contentDetails", playlistId=uploads_playlist_id, maxResults=50, pageToken=next_page_token)
            .execute()
        )

        for item in playlist_response["items"]:
            video_ids.append(item["contentDetails"]["videoId"])

        next_page_token = playlist_response.get("nextPageToken")
        if not next_page_token:
            break

    return video_ids


def create_table(table, headers=None):
    if headers:
        headstring = "\t{}\t" * len(headers)
        print(headstring.format(*headers))

    rowstring = "\t{}\t" * len(table[0])
    for row in table:
        print(rowstring.format(*row))


def main():
    try:
        youtube_analytics_service = get_authenticated_service(  # noqa: F841
            API_NAME="youtubeAnalytics", API_VERSION="v2"
        )
        youtube_data_service = get_authenticated_service(API_NAME="youtube", API_VERSION="v3")

        # Define the report query
        # request = get_all_video_stats(youtube_analytics, start_date="2025-01-01", end_date="2025-01-01")
        # request = youtube_analytics_service.reports().query(
        #     ids=f"channel==MINE",
        #     startDate="2025-01-01",
        #     endDate="2025-01-01",
        #     filters=f"video==tydWYO91kcI,Umh2bEiMcxU",
        #     metrics=",".join(METRICS),
        #     dimensions=",".join(DIMENSIONS),
        #     sort="-views",
        # )
        # response = request.execute()
        # headers = METRICS
        # create_table(response.get("rows", []), headers=headers)
        # print("Successfully retrieved Analytics data:")

        # currently has a scope permission error and fails, as token doesnt have youtube.readonly
        get_all_video_ids_response = get_all_video_ids(youtube_data_service)
        print(get_all_video_ids_response)

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
