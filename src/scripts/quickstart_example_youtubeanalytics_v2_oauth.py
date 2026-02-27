# -*- coding: utf-8 -*-

import json
import googleapiclient.discovery
from google.oauth2.credentials import Credentials
from google.cloud import secretmanager
import pprint
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

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

def get_video_ids(youtube_service, start_date, end_date) -> dict:
    """Returns all videos IDS with greater than 0 views for the given date range, and the vie count for each video.
    Note: The YouTube Analytics API has a maximum of 200 rows per request, we also can't exclude video id or use startIndex to paginate
    """
    # returns all videos with greater than 0 views for the given date range
    # uses startIndex pagination; note: startIndex + maxResults cannot exceed 50,000
    max_results = 200




    response = youtube_service.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics="views",
        maxResults=max_results,  # YouTube Analytics API has a max limit of 200
        dimensions="video",
        sort="-views",  # must specify sort or query fails
    ).execute()

    rows = response.get("rows", [])
    if len(rows) >= 200:
        print(f"Warning: Retrieved {len(rows)} rows, which is the maximum per request. Consider shortening the date range to ensure you retrieve all data.")
    
    return dict(rows)

def create_table(table, headers=None):
    if headers:
        headstring = "\t{}\t" * len(headers)
        print(headstring.format(*headers))
    
    rowstring = "\t{}\t" * len(table[0])
    for row in table: 
        print(rowstring.format(*row))


def generate_date_ranges(start_date:str, end_date:str,days_delta:int=7):
    """Generates a list of date ranges (start_date, end_date) for each month between start_year and end_year."""
    start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    delta = timedelta(days=days_delta)
    date_ranges = []
    while start_date <= end_date:
        date_ranges.append(start_date.isoformat())
        start_date += delta
    return list(zip(date_ranges[0:-1], date_ranges[1:]))

def main():
    try:
        youtube_analytics = get_authenticated_service()

        # Define the report query
        # request = get_video_ids(youtube_analytics, start_date="2025-01-01", end_date="2025-01-01")
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
        # metrics = get_video_ids(youtube_analytics, start_date="2025-01-01", end_date="2025-01-01")

        # metrics = get_video_ids(youtube_analytics, start_date="2025-01-01", end_date="2025-02-01")
        # metrics = get_video_ids(youtube_analytics, start_date="2021-06-01", end_date="2022-01-01")
        # metrics = get_video_ids(youtube_analytics, start_date="2022-01-01", end_date="2022-06-01")
        # metrics = get_video_ids(youtube_analytics, start_date="2023-01-01", end_date="2023-06-01")
        # metrics = get_video_ids(youtube_analytics, start_date="2024-01-01", end_date="2024-06-01")

        sample_date_ranges = generate_date_ranges(start_date="2021-01-01", end_date="2026-02-01", days_delta=7)
        # start_end_date_pairs = list(zip(dates[0:-1:], dates[1:]))
        # print(start_end_date_pairs)

        # sample_date_ranges = [('2021-01-01', '2021-01-08'), ('2021-01-08', '2021-01-15'), ('2021-01-15', '2021-01-22'), ('2021-01-22', '2021-01-29'), ('2021-01-29', '2021-02-05')]
        # sample_date_ranges = [('2021-01-01', '2021-01-08'), ('2021-01-08', '2021-01-15'), ('2021-01-15', '2021-01-22'), ('2021-01-22', '2021-01-29'), ('2021-01-29', '2021-02-05'), ('2021-02-05', '2021-02-12'), ('2021-02-12', '2021-02-19'), ('2021-02-19', '2021-02-26'), ('2021-02-26', '2021-03-05'), ('2021-03-05', '2021-03-12'), ('2021-03-12', '2021-03-19'), ('2021-03-19', '2021-03-26'), ('2021-03-26', '2021-04-02'), ('2021-04-02', '2021-04-09'), ('2021-04-09', '2021-04-16'), ('2021-04-16', '2021-04-23'), ('2021-04-23', '2021-04-30'), ('2021-04-30', '2021-05-07'), ('2021-05-07', '2021-05-14'), ('2021-05-14', '2021-05-21'), ('2021-05-21', '2021-05-28'), ('2021-05-28', '2021-06-04'), ('2021-06-04', '2021-06-11'), ('2021-06-11', '2021-06-18'), ('2021-06-18', '2021-06-25'), ('2021-06-25', '2021-07-02'), ('2021-07-02', '2021-07-09'), ('2021-07-09', '2021-07-16'), ('2021-07-16', '2021-07-23'), ('2021-07-23', '2021-07-30'), ('2021-07-30', '2021-08-06'), ('2021-08-06', '2021-08-13'), ('2021-08-13', '2021-08-20'), ('2021-08-20', '2021-08-27'), ('2021-08-27', '2021-09-03'), ('2021-09-03', '2021-09-10'), ('2021-09-10', '2021-09-17'), ('2021-09-17', '2021-09-24'), ('2021-09-24', '2021-10-01'), ('2021-10-01', '2021-10-08'), ('2021-10-08', '2021-10-15'), ('2021-10-15', '2021-10-22'), ('2021-10-22', '2021-10-29'), ('2021-10-29', '2021-11-05'), ('2021-11-05', '2021-11-12'), ('2021-11-12', '2021-11-19'), ('2021-11-19', '2021-11-26'), ('2021-11-26', '2021-12-03'), ('2021-12-03', '2021-12-10'), ('2021-12-10', '2021-12-17'), ('2021-12-17', '2021-12-24'), ('2021-12-24', '2021-12-31'), ('2021-12-31', '2022-01-07'), ('2022-01-07', '2022-01-14'), ('2022-01-14', '2022-01-21'), ('2022-01-21', '2022-01-28'), ('2022-01-28', '2022-02-04'), ('2022-02-04', '2022-02-11'), ('2022-02-11', '2022-02-18'), ('2022-02-18', '2022-02-25'), ('2022-02-25', '2022-03-04'), ('2022-03-04', '2022-03-11'), ('2022-03-11', '2022-03-18'), ('2022-03-18', '2022-03-25'), ('2022-03-25', '2022-04-01'), ('2022-04-01', '2022-04-08'), ('2022-04-08', '2022-04-15'), ('2022-04-15', '2022-04-22'), ('2022-04-22', '2022-04-29'), ('2022-04-29', '2022-05-06'), ('2022-05-06', '2022-05-13'), ('2022-05-13', '2022-05-20'), ('2022-05-20', '2022-05-27'), ('2022-05-27', '2022-06-03'), ('2022-06-03', '2022-06-10'), ('2022-06-10', '2022-06-17'), ('2022-06-17', '2022-06-24'), ('2022-06-24', '2022-07-01'), ('2022-07-01', '2022-07-08'), ('2022-07-08', '2022-07-15'), ('2022-07-15', '2022-07-22'), ('2022-07-22', '2022-07-29'), ('2022-07-29', '2022-08-05'), ('2022-08-05', '2022-08-12'), ('2022-08-12', '2022-08-19'), ('2022-08-19', '2022-08-26'), ('2022-08-26', '2022-09-02'), ('2022-09-02', '2022-09-09'), ('2022-09-09', '2022-09-16'), ('2022-09-16', '2022-09-23'), ('2022-09-23', '2022-09-30'), ('2022-09-30', '2022-10-07'), ('2022-10-07', '2022-10-14'), ('2022-10-14', '2022-10-21'), ('2022-10-21', '2022-10-28'), ('2022-10-28', '2022-11-04'), ('2022-11-04', '2022-11-11'), ('2022-11-11', '2022-11-18'), ('2022-11-18', '2022-11-25'), ('2022-11-25', '2022-12-02'), ('2022-12-02', '2022-12-09'), ('2022-12-09', '2022-12-16'), ('2022-12-16', '2022-12-23'), ('2022-12-23', '2022-12-30'), ('2022-12-30', '2023-01-06'), ('2023-01-06', '2023-01-13'), ('2023-01-13', '2023-01-20'), ('2023-01-20', '2023-01-27'), ('2023-01-27', '2023-02-03'), ('2023-02-03', '2023-02-10'), ('2023-02-10', '2023-02-17'), ('2023-02-17', '2023-02-24'), ('2023-02-24', '2023-03-03'), ('2023-03-03', '2023-03-10'), ('2023-03-10', '2023-03-17'), ('2023-03-17', '2023-03-24'), ('2023-03-24', '2023-03-31'), ('2023-03-31', '2023-04-07'), ('2023-04-07', '2023-04-14'), ('2023-04-14', '2023-04-21'), ('2023-04-21', '2023-04-28'), ('2023-04-28', '2023-05-05'), ('2023-05-05', '2023-05-12'), ('2023-05-12', '2023-05-19'), ('2023-05-19', '2023-05-26'), ('2023-05-26', '2023-06-02'), ('2023-06-02', '2023-06-09'), ('2023-06-09', '2023-06-16'), ('2023-06-16', '2023-06-23'), ('2023-06-23', '2023-06-30'), ('2023-06-30', '2023-07-07'), ('2023-07-07', '2023-07-14'), ('2023-07-14', '2023-07-21'), ('2023-07-21', '2023-07-28'), ('2023-07-28', '2023-08-04'), ('2023-08-04', '2023-08-11'), ('2023-08-11', '2023-08-18'), ('2023-08-18', '2023-08-25'), ('2023-08-25', '2023-09-01'), ('2023-09-01', '2023-09-08'), ('2023-09-08', '2023-09-15'), ('2023-09-15', '2023-09-22'), ('2023-09-22', '2023-09-29'), ('2023-09-29', '2023-10-06'), ('2023-10-06', '2023-10-13'), ('2023-10-13', '2023-10-20'), ('2023-10-20', '2023-10-27'), ('2023-10-27', '2023-11-03'), ('2023-11-03', '2023-11-10'), ('2023-11-10', '2023-11-17'), ('2023-11-17', '2023-11-24'), ('2023-11-24', '2023-12-01'), ('2023-12-01', '2023-12-08'), ('2023-12-08', '2023-12-15'), ('2023-12-15', '2023-12-22'), ('2023-12-22', '2023-12-29'), ('2023-12-29', '2024-01-05'), ('2024-01-05', '2024-01-12'), ('2024-01-12', '2024-01-19'), ('2024-01-19', '2024-01-26'), ('2024-01-26', '2024-02-02'), ('2024-02-02', '2024-02-09'), ('2024-02-09', '2024-02-16'), ('2024-02-16', '2024-02-23'), ('2024-02-23', '2024-03-01'), ('2024-03-01', '2024-03-08'), ('2024-03-08', '2024-03-15'), ('2024-03-15', '2024-03-22'), ('2024-03-22', '2024-03-29'), ('2024-03-29', '2024-04-05'), ('2024-04-05', '2024-04-12'), ('2024-04-12', '2024-04-19'), ('2024-04-19', '2024-04-26'), ('2024-04-26', '2024-05-03'), ('2024-05-03', '2024-05-10'), ('2024-05-10', '2024-05-17'), ('2024-05-17', '2024-05-24'), ('2024-05-24', '2024-05-31'), ('2024-05-31', '2024-06-07'), ('2024-06-07', '2024-06-14'), ('2024-06-14', '2024-06-21'), ('2024-06-21', '2024-06-28'), ('2024-06-28', '2024-07-05'), ('2024-07-05', '2024-07-12'), ('2024-07-12', '2024-07-19'), ('2024-07-19', '2024-07-26'), ('2024-07-26', '2024-08-02'), ('2024-08-02', '2024-08-09'), ('2024-08-09', '2024-08-16'), ('2024-08-16', '2024-08-23'), ('2024-08-23', '2024-08-30'), ('2024-08-30', '2024-09-06'), ('2024-09-06', '2024-09-13'), ('2024-09-13', '2024-09-20'), ('2024-09-20', '2024-09-27'), ('2024-09-27', '2024-10-04'), ('2024-10-04', '2024-10-11'), ('2024-10-11', '2024-10-18'), ('2024-10-18', '2024-10-25'), ('2024-10-25', '2024-11-01'), ('2024-11-01', '2024-11-08'), ('2024-11-08', '2024-11-15'), ('2024-11-15', '2024-11-22'), ('2024-11-22', '2024-11-29'), ('2024-11-29', '2024-12-06'), ('2024-12-06', '2024-12-13'), ('2024-12-13', '2024-12-20'), ('2024-12-20', '2024-12-27'), ('2024-12-27', '2025-01-03'), ('2025-01-03', '2025-01-10'), ('2025-01-10', '2025-01-17'), ('2025-01-17', '2025-01-24'), ('2025-01-24', '2025-01-31'), ('2025-01-31', '2025-02-07'), ('2025-02-07', '2025-02-14'), ('2025-02-14', '2025-02-21'), ('2025-02-21', '2025-02-28'), ('2025-02-28', '2025-03-07'), ('2025-03-07', '2025-03-14'), ('2025-03-14', '2025-03-21'), ('2025-03-21', '2025-03-28'), ('2025-03-28', '2025-04-04'), ('2025-04-04', '2025-04-11'), ('2025-04-11', '2025-04-18'), ('2025-04-18', '2025-04-25'), ('2025-04-25', '2025-05-02'), ('2025-05-02', '2025-05-09'), ('2025-05-09', '2025-05-16'), ('2025-05-16', '2025-05-23'), ('2025-05-23', '2025-05-30'), ('2025-05-30', '2025-06-06'), ('2025-06-06', '2025-06-13'), ('2025-06-13', '2025-06-20'), ('2025-06-20', '2025-06-27'), ('2025-06-27', '2025-07-04'), ('2025-07-04', '2025-07-11'), ('2025-07-11', '2025-07-18'), ('2025-07-18', '2025-07-25'), ('2025-07-25', '2025-08-01'), ('2025-08-01', '2025-08-08'), ('2025-08-08', '2025-08-15'), ('2025-08-15', '2025-08-22'), ('2025-08-22', '2025-08-29'), ('2025-08-29', '2025-09-05'), ('2025-09-05', '2025-09-12'), ('2025-09-12', '2025-09-19'), ('2025-09-19', '2025-09-26'), ('2025-09-26', '2025-10-03'), ('2025-10-03', '2025-10-10'), ('2025-10-10', '2025-10-17'), ('2025-10-17', '2025-10-24'), ('2025-10-24', '2025-10-31'), ('2025-10-31', '2025-11-07'), ('2025-11-07', '2025-11-14'), ('2025-11-14', '2025-11-21'), ('2025-11-21', '2025-11-28'), ('2025-11-28', '2025-12-05'), ('2025-12-05', '2025-12-12'), ('2025-12-12', '2025-12-19'), ('2025-12-19', '2025-12-26'), ('2025-12-26', '2026-01-02'), ('2026-01-02', '2026-01-09'), ('2026-01-09', '2026-01-16'), ('2026-01-16', '2026-01-23'), ('2026-01-23', '2026-01-30')]
        youtube_id_set = set()
        for start_date, end_date in sample_date_ranges:
            print(start_date, end_date)
            # print(f"Retrieving data for date range: {start_date} to {end_date}")
            metrics = get_video_ids(youtube_analytics, start_date=start_date, end_date=end_date)
            youtube_id_set.update(list(metrics.keys()))
            # print(f"Retrieved {len(metrics)} rows for date range: {start_date} to {end_date}")
        print(len(youtube_id_set))
        print(youtube_id_set)


        ####
        # credentials = youtube_analytics._http.credentials

        # def fetch_range(start, end):
        #     service = googleapiclient.discovery.build("youtubeAnalytics", "v2", credentials=credentials)
        #     return get_video_ids(service, start, end)

        # youtube_id_set = set()
        # with ThreadPoolExecutor(max_workers=5) as executor:
        #     futures = {
        #         executor.submit(fetch_range, start, end): (start, end)
        #         for start, end in sample_date_ranges
        #     }
        #     for future in as_completed(futures):
        #         youtube_id_set.update(future.result().keys())

        print(len(youtube_id_set))







    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()