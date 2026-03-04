# -*- coding: utf-8 -*-

import json
import googleapiclient.discovery
from google.oauth2.credentials import Credentials
from google.cloud import secretmanager

PROJECT_ID = "1031179907628"
TOKEN_JSON_SECRET = "youtube-processing-web-client-token-json" 
CLIENT_ID_STRING_SECRET = "youtube-processing-client-id-web-app"
CLIENT_SECRET_STRING_SECRET = "youtube-processing-client-secret-web-app"
CHANNEL_ID = "UCSNK6abAoM6Kj0SkHOStNLg"

METRICS = "engagedViews,views,estimatedMinutesWatched,likes,subscribersGained,subscribersLost,shares,comments,averageViewDuration,averageViewPercentage" 

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
        request = youtube_analytics.reports().query(
            ids=f"channel=={CHANNEL_ID}",
            startDate="2025-01-01",
            endDate="2025-01-10",
            metrics=METRICS,
            dimensions="day",
            sort="day",
        )

        # .execute() handles the 401/refresh logic automatically!
        response = request.execute()

        headers = METRICS.split(",")
        create_table(response.get("rows", []), headers=headers)

        print("Successfully retrieved Analytics data:")


    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()