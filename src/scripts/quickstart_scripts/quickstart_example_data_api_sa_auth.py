# -*- coding: utf-8 -*-

import json

import googleapiclient.discovery
import googleapiclient.errors
from google.cloud import secretmanager
from google.oauth2 import service_account

PROJECT_ID = "1031179907628"
SERVICE_ACCOUNT_SECRET_ID = "youtube-ingestion-api-sa-key"


def get_gcp_secret(secret_name, project_id=PROJECT_ID):
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    payload = client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")
    return payload


def get_credentials(scopes):
    sa_json = get_gcp_secret(SERVICE_ACCOUNT_SECRET_ID)
    info = json.loads(sa_json)
    return service_account.Credentials.from_service_account_info(info, scopes=scopes)


CHANNEL_ID = "UCSNK6abAoM6Kj0SkHOStNLg"


def main():
    scopes = ["https://www.googleapis.com/auth/yt-analytics.readonly"]

    credentials = get_credentials(scopes)
    youtube_analytics = googleapiclient.discovery.build("youtubeAnalytics", "v2", credentials=credentials)

    request = youtube_analytics.reports().query(
        ids=f"channel=={CHANNEL_ID}",
        startDate="2024-01-01",
        endDate="2024-12-31",
        metrics="estimatedMinutesWatched,views,likes,subscribersGained",
        dimensions="day",
        sort="day",
    )
    response = request.execute()

    print(response)


if __name__ == "__main__":
    main()
