# -*- coding: utf-8 -*-

import logging
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from src.main import (
    _iso8601_duration_to_seconds,
    _run_country_report,
    _run_report,
    _with_country_code,
    _with_video_id,
    chunk_video_ids,
    generate_date_ranges,
    get_operating_system_and_device_type_single_video_config,
    get_playback_details_single_video_config,
    get_playback_locations_single_video_config,
    get_top_200_videos_for_date_range,
    get_traffic_sources_single_video_config,
    get_user_activity_single_video_single_day,
    get_user_activity_single_video_config,
    get_video_metadata,
    get_video_titles,
    get_youtube_analytics_api_v2_service,
    get_youtube_data_api_v3_service,
    load_data_to_bq,
    OPERATING_SYSTEM_AND_DEVICE_TYPE_BQ_SCHEMA,
    PLAYBACK_DETAILS_BQ_SCHEMA,
    PLAYBACK_LOCATION_BQ_SCHEMA,
    TRAFFIC_SOURCES_BQ_SCHEMA,
    USER_ACTIVITY_BQ_SCHEMA,
    USER_ACTIVITY_WITH_COUNTRY_CODE_BQ_SCHEMA,
    VIDEO_CATEGORY_ID_TO_TITLE,
)


# ---------------------------------------------------------------------------
# _iso8601_duration_to_seconds
# ---------------------------------------------------------------------------

class TestIso8601DurationToSeconds:
    def test_hours_minutes_seconds(self) -> None:
        assert _iso8601_duration_to_seconds("PT1H2M3S") == 3723

    def test_minutes_seconds(self) -> None:
        assert _iso8601_duration_to_seconds("PT10M30S") == 630

    def test_hours_only(self) -> None:
        assert _iso8601_duration_to_seconds("PT2H") == 7200

    def test_minutes_only(self) -> None:
        assert _iso8601_duration_to_seconds("PT45M") == 2700

    def test_seconds_only(self) -> None:
        assert _iso8601_duration_to_seconds("PT30S") == 30

    def test_zero_duration(self) -> None:
        assert _iso8601_duration_to_seconds("PT0S") == 0

    def test_empty_string_returns_none(self) -> None:
        assert _iso8601_duration_to_seconds("") is None

    def test_none_returns_none(self) -> None:
        assert _iso8601_duration_to_seconds(None) is None

    def test_invalid_format_returns_none(self) -> None:
        assert _iso8601_duration_to_seconds("not-a-duration") is None

    def test_missing_pt_prefix_returns_none(self) -> None:
        assert _iso8601_duration_to_seconds("1H2M3S") is None


# ---------------------------------------------------------------------------
# generate_date_ranges
# ---------------------------------------------------------------------------

class TestGenerateDateRanges:
    def test_basic_weekly_ranges(self) -> None:
        result = generate_date_ranges("2021-01-01", "2021-01-22", days_delta=7)
        assert result == [
            ("2021-01-01", "2021-01-08"),
            ("2021-01-08", "2021-01-15"),
            ("2021-01-15", "2021-01-22"),
        ]

    def test_single_range(self) -> None:
        result = generate_date_ranges("2021-01-01", "2021-01-08", days_delta=7)
        assert result == [("2021-01-01", "2021-01-08")]

    def test_custom_delta(self) -> None:
        result = generate_date_ranges("2021-01-01", "2021-01-04", days_delta=1)
        assert len(result) == 3
        assert result[0] == ("2021-01-01", "2021-01-02")
        assert result[-1] == ("2021-01-03", "2021-01-04")

    def test_same_start_end_returns_empty(self) -> None:
        result = generate_date_ranges("2021-01-01", "2021-01-01", days_delta=7)
        assert result == []

    def test_end_before_start_returns_empty(self) -> None:
        result = generate_date_ranges("2021-02-01", "2021-01-01", days_delta=7)
        assert result == []

    def test_monthly_delta(self) -> None:
        result = generate_date_ranges("2021-01-01", "2021-04-01", days_delta=30)
        assert len(result) == 3
        # Each range should span 30 days
        for start, end in result:
            start_d = datetime.strptime(start, "%Y-%m-%d").date()
            end_d = datetime.strptime(end, "%Y-%m-%d").date()
            assert (end_d - start_d).days == 30


# ---------------------------------------------------------------------------
# chunk_video_ids
# ---------------------------------------------------------------------------

class TestChunkVideoIds:
    def test_empty_set(self) -> None:
        assert chunk_video_ids(set()) == []

    def test_fewer_than_chunk_size(self) -> None:
        ids = {"a", "b", "c"}
        result = chunk_video_ids(ids, chunk_size=50)
        assert len(result) == 1
        assert set(result[0]) == ids

    def test_exact_chunk_size(self) -> None:
        ids = {str(i) for i in range(50)}
        result = chunk_video_ids(ids, chunk_size=50)
        assert len(result) == 1
        assert len(result[0]) == 50

    def test_multiple_chunks(self) -> None:
        ids = {str(i) for i in range(120)}
        result = chunk_video_ids(ids, chunk_size=50)
        assert len(result) == 3
        assert len(result[0]) == 50
        assert len(result[1]) == 50
        assert len(result[2]) == 20
        # All IDs should be present
        all_ids = {item for chunk in result for item in chunk}
        assert all_ids == ids

    def test_chunk_size_one(self) -> None:
        ids = {"a", "b", "c"}
        result = chunk_video_ids(ids, chunk_size=1)
        assert len(result) == 3
        assert all(len(chunk) == 1 for chunk in result)

# ---------------------------------------------------------------------------
# Integration tests — use YouTube APIs to verify auth works
# ---------------------------------------------------------------------------

class TestYouTubeAPIIntegration:
    """These tests authenticate against the real YouTube APIs.
    They require valid GCP credentials and a cached/refreshable OAuth token.
    Mark with 'integration' so they can be skipped in CI with: pytest -m "not integration"
    """

    SMALL_SAMPLE_VIDEO_IDS = [
        "4_1gld1PIkA", "ucjmWjJ25Ho", "USo1yHS5th0", "GY-NpWFyu8w",
        "xUZZKi94p6Q", "RqZ9e1GXJLo", "hs8sykCaf3E", "I5gP62TX8aQ",
        "1whaQq44Pew", "oSZPWrZDINQ",
    ]

    @pytest.mark.integration
    def test_run_report_user_activity(self) -> None:
        """_run_report with user_activity config returns 10 rows for the small sample on 2025-01-01."""
        analytics_service = get_youtube_analytics_api_v2_service()
        result = _run_report(
            analytics_service, self.SMALL_SAMPLE_VIDEO_IDS, "2025-01-01", "2025-01-01",
            USER_ACTIVITY_BQ_SCHEMA, get_user_activity_single_video_config, "User Activity",
        )
        assert isinstance(result, pa.Table)
        assert result.num_rows == 10
        expected_columns = list(_with_video_id(USER_ACTIVITY_BQ_SCHEMA).keys())
        assert result.column_names == expected_columns

    @pytest.mark.integration
    def test_run_report_playback_details(self) -> None:
        """_run_report with playback_details config returns 10 rows for the small sample on 2025-01-01."""
        analytics_service = get_youtube_analytics_api_v2_service()
        result = _run_report(
            analytics_service, self.SMALL_SAMPLE_VIDEO_IDS, "2025-01-01", "2025-01-01",
            PLAYBACK_DETAILS_BQ_SCHEMA, get_playback_details_single_video_config, "Playback Details",
        )
        assert isinstance(result, pa.Table)
        assert result.num_rows == 10
        expected_columns = list(_with_video_id(PLAYBACK_DETAILS_BQ_SCHEMA).keys())
        assert result.column_names == expected_columns

    @pytest.mark.integration
    def test_run_report_playback_location(self) -> None:
        """_run_report with playback_location config returns 18 rows for the small sample on 2025-01-01."""
        analytics_service = get_youtube_analytics_api_v2_service()
        result = _run_report(
            analytics_service, self.SMALL_SAMPLE_VIDEO_IDS, "2025-01-01", "2025-01-01",
            PLAYBACK_LOCATION_BQ_SCHEMA, get_playback_locations_single_video_config, "Playback Location",
        )
        assert isinstance(result, pa.Table)
        assert result.num_rows == 18
        expected_columns = list(_with_video_id(PLAYBACK_LOCATION_BQ_SCHEMA).keys())
        assert result.column_names == expected_columns

    @pytest.mark.integration
    def test_run_report_traffic_sources(self) -> None:
        """_run_report with traffic_sources config returns 28 rows for the small sample on 2025-01-01."""
        analytics_service = get_youtube_analytics_api_v2_service()
        result = _run_report(
            analytics_service, self.SMALL_SAMPLE_VIDEO_IDS, "2025-01-01", "2025-01-01",
            TRAFFIC_SOURCES_BQ_SCHEMA, get_traffic_sources_single_video_config, "Traffic Sources",
        )
        assert isinstance(result, pa.Table)
        assert result.num_rows == 28
        expected_columns = list(_with_video_id(TRAFFIC_SOURCES_BQ_SCHEMA).keys())
        assert result.column_names == expected_columns

    @pytest.mark.integration
    def test_run_report_operating_system_and_device(self) -> None:
        """_run_report with operating_system_and_device config returns 40 rows for the small sample on 2025-01-01."""
        analytics_service = get_youtube_analytics_api_v2_service()
        result = _run_report(
            analytics_service, self.SMALL_SAMPLE_VIDEO_IDS, "2025-01-01", "2025-01-01",
            OPERATING_SYSTEM_AND_DEVICE_TYPE_BQ_SCHEMA,
            get_operating_system_and_device_type_single_video_config, "Operating System and Device",
        )
        assert isinstance(result, pa.Table)
        assert result.num_rows == 40
        expected_columns = list(_with_video_id(OPERATING_SYSTEM_AND_DEVICE_TYPE_BQ_SCHEMA).keys())
        assert result.column_names == expected_columns

    @pytest.mark.integration
    def test_run_country_report(self) -> None:
        """_run_country_report returns rows with country_code column for GB and US on 2025-01-01."""
        analytics_service = get_youtube_analytics_api_v2_service()
        result = _run_country_report(
            analytics_service, self.SMALL_SAMPLE_VIDEO_IDS, "2025-01-01", "2025-01-01", ["GB", "US"],
        )
        assert isinstance(result, pa.Table)
        assert result.num_rows == 3
        expected_columns = list(_with_country_code(_with_video_id(USER_ACTIVITY_WITH_COUNTRY_CODE_BQ_SCHEMA)).keys())
        assert result.column_names == expected_columns
        assert set(result.column("country_code").to_pylist()).issubset({"GB"})

    @pytest.mark.integration
    def test_analytics_api_returns_top_videos(self) -> None:
        """Authenticate via the Analytics API and retrieve the most-viewed
        videos for a short date range."""
        analytics_service = get_youtube_analytics_api_v2_service()
        result = get_top_200_videos_for_date_range(
            analytics_service,
            start_date="2024-01-01",
            end_date="2024-01-08",
        )
        assert isinstance(result, pa.Table)
        assert "video" in result.column_names
        assert result.num_rows > 0

    @pytest.mark.integration
    def test_data_api_returns_video_title(self) -> None:
        """Authenticate via the Data API and fetch the title of a known video."""
        data_service = get_youtube_data_api_v3_service()
        result = get_video_titles(data_service, ["zbkxWchTyBU"])
        assert isinstance(result, pa.Table)
        assert result.num_rows == 1
        assert result.column("video_id").to_pylist() == ["zbkxWchTyBU"]
        # Verify we got a non-empty title back
        title = result.column("video_title").to_pylist()[0]
        assert isinstance(title, str)
        assert len(title) > 0
