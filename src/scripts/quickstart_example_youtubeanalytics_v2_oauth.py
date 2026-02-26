# -*- coding: utf-8 -*-

import json
import googleapiclient.discovery
from google.oauth2.credentials import Credentials
from google.cloud import secretmanager
import pprint

PROJECT_ID = "1031179907628"
TOKEN_JSON_SECRET = "youtube-processing-web-client-token-json" 
CLIENT_ID_STRING_SECRET = "youtube-processing-client-id-web-app"
CLIENT_SECRET_STRING_SECRET = "youtube-processing-client-secret-web-app"
CHANNEL_ID = "UCSNK6abAoM6Kj0SkHOStNLg"

# METRICS = ["engagedViews","views","estimatedMinutesWatched","likes","subscribersGained","subscribersLost","shares","comments","averageViewDuration","averageViewPercentage"]
METRICS= ["views"]
# DIMENSIONS = ["day", "gender","deviceType","video","ageGroup"]
DIMENSIONS = ["video"]


def get_gcp_secret(secret_name, project_id=PROJECT_ID):
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    payload = client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")
    return payload

def get_authenticated_service():

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
        scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"]
    )

    return googleapiclient.discovery.build("youtubeAnalytics", "v2", credentials=credentials)

def get_all_video_stats(youtube_service, start_date, end_date):
    # returns all videos with create then 0 views, which the YouTube Analytics API does not return by default
    max_results = 200
    request = youtube_service.reports().query(
        ids=f"channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics="views",
        maxResults=max_results, # YouTube Analytics API has a max limit of 200
        dimensions="video",
        sort="-views", # have to specify descending sort order or else query fails
    )
    
    response = request.execute()
    return response

def create_table(table, headers=None):
    if headers:
        headstring = "\t{}\t" * len(headers)
        print(headstring.format(*headers))
    
    rowstring = "\t{}\t" * len(table[0])
    for row in table: 
        print(rowstring.format(*row))

def main():
    try:
        youtube_analytics = get_authenticated_service()

        # Define the report query
        # request = get_all_video_stats(youtube_analytics, start_date="2025-01-01", end_date="2025-01-01")
        # request = youtube_analytics.reports().query(
        #     ids=f"channel==MINE",
        #     startDate="2025-01-01",
        #     endDate="2026-01-01",
        #     # metrics=",".join(METRICS),
        #     metrics="views",
        #     maxResults=200, # YouTube Analytics API has a max limit of 200
        #     # dimensions=",".join(DIMENSIONS),
        #     dimensions="video",
        #     sort="-views",
        # )

        # response = request.execute()

        # headers = METRICS
        # create_table(response.get("rows", []), headers=headers)
        # print(f"Total rows retrieved: {len(response.get('rows', []))}")

        metrics = get_all_video_stats(youtube_analytics, start_date="2024-01-01", end_date="2026-01-01")
        pprint.pp(metrics)
        print(f'Num videos with views: {len(metrics['rows'])}')
        print("Successfully retrieved Analytics data:")


    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()