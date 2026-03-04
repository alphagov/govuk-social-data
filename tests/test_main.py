# -*- coding: utf-8 -*-

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from src.main import (
    _iso8601_duration_to_seconds,
    chunk_video_ids,
    create_table,
    generate_date_ranges,
    get_top_200_videos_for_date_range,
    get_user_activity_single_video_single_day,
    get_video_metadata,
    get_video_titles,
    get_youtube_analytics_api_v2_service,
    get_youtube_data_api_v3_service,
    load_data_to_bq,
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
# get_video_titles (mocked API)
# ---------------------------------------------------------------------------

class TestGetVideoTitles:
    def test_returns_table_with_titles(self) -> None:
        mock_service = MagicMock()
        mock_service.videos().list().execute.return_value = {
            "items": [
                {"id": "vid1", "snippet": {"title": "Title One"}},
                {"id": "vid2", "snippet": {"title": "Title Two"}},
            ]
        }
        result = get_video_titles(mock_service, ["vid1", "vid2"])
        assert isinstance(result, pa.Table)
        assert result.num_rows == 2
        assert result.column("video_id").to_pylist() == ["vid1", "vid2"]
        assert result.column("video_title").to_pylist() == ["Title One", "Title Two"]

    def test_empty_ids_returns_empty_table(self) -> None:
        mock_service = MagicMock()
        mock_service.videos().list().execute.return_value = {"items": []}
        result = get_video_titles(mock_service, [])
        assert isinstance(result, pa.Table)
        assert result.num_rows == 0


# ---------------------------------------------------------------------------
# get_video_metadata (mocked API)
# ---------------------------------------------------------------------------

class TestGetVideoMetadata:
    def _make_api_item(self) -> dict:
        return {
            "id": "vid1",
            "snippet": {
                "title": "Test Video",
                "publishedAt": "2021-01-01T00:00:00Z",
                "description": "A description",
                "tags": ["tag1", "tag2"],
                "categoryId": "28",
                "channelTitle": "MyChannel",
                "channelId": "UC123",
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "contentDetails": {
                "duration": "PT10M30S",
                "definition": "hd",
                "caption": "false",
            },
            "statistics": {
                "viewCount": "1000",
                "likeCount": "50",
                "commentCount": "10",
                "favoriteCount": "0",
            },
            "status": {
                "privacyStatus": "public",
                "madeForKids": False,
                "embeddable": True,
            },
        }

    def test_returns_correct_columns(self) -> None:
        mock_service = MagicMock()
        mock_service.videos().list().execute.return_value = {
            "items": [self._make_api_item()]
        }
        result = get_video_metadata(mock_service, ["vid1"])
        assert isinstance(result, pa.Table)
        assert result.num_rows == 1
        assert result.column("video_id").to_pylist() == ["vid1"]
        assert result.column("title").to_pylist() == ["Test Video"]
        assert result.column("duration_in_seconds").to_pylist() == [630]
        assert result.column("viewCount").to_pylist() == [1000]
        assert result.column("categoryTitle").to_pylist() == ["Science & Technology"]
        assert result.column("tags").to_pylist() == [["tag1", "tag2"]]

    def test_handles_missing_optional_fields(self) -> None:
        mock_service = MagicMock()
        mock_service.videos().list().execute.return_value = {
            "items": [{
                "id": "vid2",
                "snippet": {},
                "contentDetails": {},
                "statistics": {},
                "status": {},
            }]
        }
        result = get_video_metadata(mock_service, ["vid2"])
        assert result.num_rows == 1
        assert result.column("video_id").to_pylist() == ["vid2"]
        assert result.column("title").to_pylist() == [None]
        assert result.column("viewCount").to_pylist() == [None]
        assert result.column("tags").to_pylist() == [[]]


# ---------------------------------------------------------------------------
# get_top_200_videos_for_date_range (mocked API)
# ---------------------------------------------------------------------------

class TestGetTop200VideosForDateRange:
    def test_returns_video_ids_table(self) -> None:
        mock_service = MagicMock()
        mock_service.reports().query().execute.return_value = {
            "columnHeaders": [
                {"name": "video"},
                {"name": "views"},
            ],
            "rows": [
                ["vid1", 500],
                ["vid2", 300],
            ],
        }
        result = get_top_200_videos_for_date_range(mock_service, "2021-01-01", "2021-01-08")
        assert isinstance(result, pa.Table)
        assert "video" in result.column_names
        assert "views" not in result.column_names
        assert result.column("video").to_pylist() == ["vid1", "vid2"]

    def test_empty_response(self) -> None:
        mock_service = MagicMock()
        mock_service.reports().query().execute.return_value = {
            "columnHeaders": [
                {"name": "video"},
                {"name": "views"},
            ],
            "rows": [],
        }
        result = get_top_200_videos_for_date_range(mock_service, "2021-01-01", "2021-01-08")
        assert result.num_rows == 0

    def test_warns_at_200_rows(self, capsys: pytest.CaptureFixture[str]) -> None:
        mock_service = MagicMock()
        rows = [[f"vid{i}", i] for i in range(200)]
        mock_service.reports().query().execute.return_value = {
            "columnHeaders": [
                {"name": "video"},
                {"name": "views"},
            ],
            "rows": rows,
        }
        get_top_200_videos_for_date_range(mock_service, "2021-01-01", "2021-01-08")
        output = capsys.readouterr().out
        assert "Warning" in output


# ---------------------------------------------------------------------------
# get_user_activity_single_video_single_day (mocked API)
# ---------------------------------------------------------------------------

class TestGetUserActivitySingleVideoSingleDay:
    def test_returns_activity_table(self) -> None:
        mock_service = MagicMock()
        mock_service.reports().query().execute.return_value = {
            "columnHeaders": [
                {"name": "day"},
                {"name": "views"},
                {"name": "likes"},
            ],
            "rows": [
                ["2021-01-01", 100, 5],
            ],
        }
        result = get_user_activity_single_video_single_day(
            mock_service, "2021-01-01", "2021-01-08", "vid1"
        )
        assert isinstance(result, pa.Table)
        assert result.num_rows == 1
        assert result.column("views").to_pylist() == [100]

    def test_empty_response(self) -> None:
        mock_service = MagicMock()
        mock_service.reports().query().execute.return_value = {
            "columnHeaders": [
                {"name": "day"},
                {"name": "views"},
            ],
            "rows": [],
        }
        result = get_user_activity_single_video_single_day(
            mock_service, "2021-01-01", "2021-01-08", "vid1"
        )
        assert result.num_rows == 0


# ---------------------------------------------------------------------------
# load_data_to_bq (mocked)
# ---------------------------------------------------------------------------

class TestLoadDataToBq:
    @patch("src.main.pa_bq.write_table")
    def test_loads_and_returns_row_count(self, mock_write: MagicMock) -> None:
        table = pa.table({"video_id": ["a", "b"], "video_title": ["t1", "t2"]})
        schema = {"video_id": pa.string(), "video_title": pa.string()}
        result = load_data_to_bq(table, "test_table", "test_dataset", schema)
        assert result == 2
        mock_write.assert_called_once()

    @patch("src.main.pa_bq.write_table", side_effect=Exception("BQ error"))
    def test_raises_on_failure(self, mock_write: MagicMock) -> None:
        table = pa.table({"video_id": ["a"], "video_title": ["t1"]})
        schema = {"video_id": pa.string(), "video_title": pa.string()}
        with pytest.raises(Exception, match="BQ error"):
            load_data_to_bq(table, "test_table", "test_dataset", schema)


# ---------------------------------------------------------------------------
# Integration tests — hit real YouTube APIs to verify auth works
# ---------------------------------------------------------------------------

class TestYouTubeAPIIntegration:
    """These tests authenticate against the real YouTube APIs.
    They require valid GCP credentials and a cached/refreshable OAuth token.
    Mark with 'integration' so they can be skipped in CI with: pytest -m "not integration"
    """

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
