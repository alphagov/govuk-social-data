### README

## To run unit tests

python -m pytest tests/test_main.py -v 2>&1

### Running the pipeline

```bash
poetry run python src/main.py
```

| Flag | Default | Description |
|------|---------|-------------|
| `--run-daily-reports` / `--no-run-daily-reports` | `True` | Run daily analytics reports per video |
| `--refresh-youtube-ids-and-metadata` / `--no-refresh-youtube-ids-and-metadata` | `False` | Discover new video IDs and fetch metadata from the APIs |
| `--full-dataset` / `--no-full-dataset` | `True` | Use fulldataset from BigQuery or 6 hardcoded video IDs |
| `--run-periodic-reports` / `--no-run-periodic-reports` | `False` | Run multi-month demographic, device and engagement analysis |
| `--start-date` | `2024-01-01` | Start date for queries (YYYY-MM-DD) |
| `--end-date` | `2025-01-01` | End date for queries (YYYY-MM-DD) |

Examples:
```bash
# Run daily reports for a all ids (default behaviour)
poetry run python -m src.main

# Refresh video IDs and metadata, then run daily reports for all videos
poetry run python -m src.main --refresh-youtube-ids-and-metadata --full-dataset

# Run only periodic reports for a custom date range
poetry run python src.main --no-run-daily-reports --run-periodic-reports --start-date 2025-01-01 --end-date 2026-01-01

# run only a small sample of tests 
 poetry run python -m  src.main  --no-run-periodic-reports --no-refresh-youtube-ids-and-metadata --no-full-dataset  --start-date 2026-02-01 --end-date 2026-02-01
```

### Useful commands
```bash
# Check which environment Poetry is using
poetry env info --path

# Point Poetry at a specific Python path
poetry env use /full/path/to/python

# Activate the Poetry shell
poetry shell

# Run a command inside the environment
poetry run python your_script.py
```


### Running tests
```bash
## running unit tests
poetry run pytest tests/test_main.py

# running a subset of unit tests
poetry run pytest tests/test_main.py -m "not integration"

# run tests while only returning the number passed
poetry run pytest tests/test_main.py -m "not integration" -x -q 2>&1 | tail -30

# to run only a single test by name and category
poetry run pytest -k "test_run_country_report" -m integration
```


# to get full upload playlist
https://stackoverflow.com/questions/18953499/youtube-api-to-fetch-all-videos-on-a-channel/27872244#27872244

```bash
curl "https://www.googleapis.com/youtube/v3/channels?id=UCSNK6abAoM6Kj0SkHOStNLg&part=contentDetails" \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

```bash
url "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet%2CcontentDetails&maxResults=50&playlistId=UUSNK6abAoM6Kj0SkHOStNLg" \
  -H "Authorization: Bearer <access_token>"
```
