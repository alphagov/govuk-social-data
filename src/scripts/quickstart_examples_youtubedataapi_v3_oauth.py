import json

import googleapiclient.discovery
from google.cloud import secretmanager
from google.oauth2.credentials import Credentials

PROJECT_ID = "1031179907628"
TOKEN_JSON_SECRET = "youtube-processing-web-client-token-json"
CLIENT_ID_STRING_SECRET = "youtube-processing-client-id-web-app"
CLIENT_SECRET_STRING_SECRET = "youtube-processing-client-secret-web-app"
CHANNEL_ID = "youtube_channel_id"

METRICS = (
    "engagedViews,views,estimatedMinutesWatched,likes,"
    "subscribersGained,subscribersLost,shares,comments,"
    "averageViewDuration,averageViewPercentage"
)


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
        scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
    )

    return googleapiclient.discovery.build("youtube", "v3", credentials=credentials)


def create_table(table, headers=None):
    if headers:
        headstring = "\t{}\t" * len(headers)
        print(headstring.format(*headers))

    rowstring = "\t{}\t" * len(table[0])
    for row in table:
        print(rowstring.format(*row))


def search_result(query):
    # Define the report query, this is just an example
    # https://googleapis.github.io/google-api-python-client/docs/dyn/youtube_v3.search.html
    request = youtube_analytics.search.list(  # noqa: F821
        ids=f"channel=={get_gcp_secret(CHANNEL_ID)}",
        startDate="2025-01-01",
        endDate="2025-01-10",
        metrics=METRICS,
        dimensions="day",
        sort="day",
    )
    request.execute()


def main():
    try:
        youtube_analytics = get_authenticated_service()  # noqa: F841
        response = search_result("example search query")

        # Define the report query, this is just an example
        # https://googleapis.github.io/google-api-python-client/docs/dyn/youtube_v3.search.html

        headers = METRICS.split(",")
        create_table(response.get("rows", []), headers=headers)

        print("Successfully retrieved Analytics data:")

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
