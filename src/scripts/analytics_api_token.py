# -*- coding: utf-8 -*-

import json

import requests
from google.cloud import secretmanager

PROJECT_ID = "1031179907628"

CHANNEL_ID = "UCSNK6abAoM6Kj0SkHOStNLg"
ANALYTICS_API_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
TOKEN_URI = "https://oauth2.googleapis.com/token"

TOKEN_SECRET_ID = "youtube-analytics-api-access-token"
CLIENT__ID = "youtube-processing-desktop-client-id-string"
CLIENT_SECRET = "youtube-processing-desktop-client-secret"


def get_gcp_secret(secret_name, project_id=PROJECT_ID):
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    payload = client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")
    return payload


def refresh_access_token(refresh_token, client_id, client_secret):
    response = requests.post(
        TOKEN_URI,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    if not response.ok:
        print(f"Token refresh failed {response.status_code}: {response.json()}")
        response.raise_for_status()
    return response.json()["access_token"]


def get_daily_video_stats(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "ids": f"channel=={CHANNEL_ID}",
        "startDate": "2023-09-01",
        "endDate": "2024-03-31",
        "dimensions": "day",
        "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained",
        "sort": "day",
    }
    response = requests.get(ANALYTICS_API_URL, headers=headers, params=params)
    if not response.ok:
        print(f"Error {response.status_code}: {response.json()}")
        response.raise_for_status()
    return response.json()


def main():
    token_data = json.loads(get_gcp_secret(TOKEN_SECRET_ID))
    client_id = get_gcp_secret(CLIENT__ID)
    client_secret_json = json.loads(get_gcp_secret(CLIENT_SECRET))
    client_secret = client_secret_json.get("installed", client_secret_json)["client_secret"]

    access_token = refresh_access_token(token_data["refresh_token"], client_id, client_secret)
    report_data = get_daily_video_stats(access_token)
    print(report_data)


if __name__ == "__main__":
    main()
