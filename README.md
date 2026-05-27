### README

## To run unit tests

python -m pytest tests/test_main.py -v 2>&1

## Setting up the environment (Poetry-only approach)

### Prerequisites
- [pyenv](https://github.com/pyenv/pyenv) installed
- [Poetry](https://python-poetry.org/) installed via pipx:
```bash
pipx install poetry
```

### From scratch
```bash
# 1. Install Python via pyenv and set it locally
pyenv install 3.14.3
pyenv local 3.14.3

# 2. Initialise Poetry in the project (skip if pyproject.toml already exists)
poetry init

# 3. Configure Poetry to create the .venv inside the project directory
poetry config virtualenvs.in-project true

# 4. Create the virtualenv using the pyenv-managed Python
poetry env use $(pyenv which python)

# 5. Install dependencies
poetry install
```

### If you already have an existing pyenv virtualenv
```bash
# 1. Create the named virtualenv (if not already created)
pyenv install 3.14.3
pyenv virtualenv 3.14.3 <your-environment-name>

# 2. Activate it (pyenv reads .python-version automatically)
pyenv local <your-environment-name>

# 3. Wire it up with Poetry
poetry env use $(pyenv which python)

# 4. Install dependencies
poetry install
```

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
poetry run python src/main.py

# Refresh video IDs and metadata, then run daily reports for all videos
poetry run python src/main.py --refresh-youtube-ids-and-metadata --full-dataset

# Run only periodic reports for a custom date range
poetry run python src/main.py --no-run-daily-reports --run-periodic-reports --start-date 2025-01-01 --end-date 2026-01-01
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

## Airflow Deployment

The `airflow_deployment/` folder contains a Cloud Composer 2 (managed Apache
Airflow on GCP) setup for running this pipeline on a daily schedule.

The pipeline is split into parallel Airflow tasks — one per report type — all
depending on a shared `get_video_ids` task that reads from BigQuery. Each daily
run fetches analytics for a single calendar day using Airflow's execution date.

**To deploy:** see [`airflow_deployment/README.md`](airflow_deployment/README.md)
for step-by-step instructions covering environment creation, IAM permissions,
DAG upload, and dependency installation.

> Before uploading to Composer, copy `src/main.py` into the DAGs folder as
> `youtube_pipeline.py` (see deployment README for the exact command). This is
> required because the project is not packaged as an installable Python module.
