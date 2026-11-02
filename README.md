# Development

## Prerequisites
1. Ensure you are part of the `gcp-data-infrastructure-editors@digital.cabinet-office.gov.uk` Google Group.

2. Ensure you have set you local gcloud config to gds-social-data project. You can set and check with `gcloud config get project` and `gcloud config set project gds-social-data`

3. Run `gcloud auth application-default login` to ensure you have local credentials for the scripts to execute locally

4. Ensure you have the `uv` package manager installed via `brew install uv`.

## Setup
1.
```bash
uv sync
```

2. Check the target datasets exist in project gds-social-data
 - govuk_facebook
 - govuk_facebook_dev
 - govuk_instagram
 - govuk_instagram_dev
 - govuk_youtube_channel

3. test manual hooks
```
git hook run facebook-smoke-test
git hook run instagram-smoke-tes
```

## Pre commit hook smoke tests

This is a short term fix, will move to github actions

facebook-smoke-test runs:

poetry run python -m facebook.main --post-limit 10 --video-limit 10 --dataset govuk_facebook_dev
instagram-smoke-test runs:

python -m instagram.main --target-date 2026-06-08 --media-limit 10 --dataset govuk_instagram_dev
Both pass only if exit code is 0 and the log contains "Uploaded.*rows →"

## Corporate proxy / SSL certificate errors

If you're on a machine behind a corporate TLS-inspection proxy (e.g. Zscaler),
API calls (e.g. to `graph.facebook.com`) will fail with:

```
ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate
```

This happens because the proxy re-signs HTTPS traffic with its own root CA,
which isn't in the certifi bundle Python uses by default, and macOS/Chrome
already trust that root CA in the system Keychain but Python doesn't check
there by default.

Fix: install `pip_system_certs` in your poetry venv — it patches Python's
`ssl` module to validate against the macOS system trust store instead of
only the certifi bundle:

```bash
poetry run pip install pip_system_certs
```

This isn't tracked in `pyproject.toml` since it's a machine-specific fix, not
a project dependency — reinstall it if it's ever missing after a fresh
`poetry install`.

## Authentication with google groups

Permissions are granted gcp-data-infrastructure-editors@digital.cabinet-office.gov.uk

This gives the following permissions on the dataform custom service account custom-dataform-service-accoun@gds-social-data.iam.gserviceaccount.com
- Service account User on the custom-dataform-service account
- Service account ActAs permissions

For the project gds-social-data it gives editor, which includes the rquired permissions bellow:
| roles/bigquery.dataEditor	          | Enables writing of rows to bigquery when running locally |
| roles/bigquery.jobUser              |	Required to submit BigQuery load jobs
| roles/secretmanager.secretAccessor  | Reads the 6 API secrets from Secret Manager |


## Youtube BQ short or long function
This helper function exists to check if a youtube video id is a short or a long

Endpoint: https://console.cloud.google.com/run/detail/europe-west2/bq-youtube-short-or-long/source?project=gds-social-data

Connector: yt-short-checker-conn projects/gds-social-data/locations/europe-west2/connections/yt-short-checker-conn

connector service account:bqcx-1031179907628-b88w@gcp-sa-bigquery-condel.iam.gserviceaccount.com

BQ routine: bq_youtube_short_or_long https://console.cloud.google.com/bigquery?project=gds-social-data&ws=!1m6!1m5!6m3!1sgds-social-data!2sdataform_youtube_processing!3sbq_youtube_short_or_long!23sTREE_NODE_SELECTION

BQ routine source code:

```
CREATE OR REPLACE FUNCTION
	`gds-social-data.dataform_youtube_processing.bq_youtube_short_or_long`(video_id STRING) RETURNS STRING
REMOTE WITH CONNECTION `gds-social-data.europe-west2.yt-short-checker-conn`
OPTIONS (endpoint = 'https://bq-youtube-short-or-long-1031179907628.europe-west2.run.app', max_batching_rows = 10);
```
Cloud Function configuration

- Startup CPU boost: Enabled
- Concurrency: 8
- Request timeout: 300 seconds
- Execution environment: Second generation

Revision
- max. instances: 100
- Auto-scaling factors
- Default auto-scaling factors
-  Image
europe-west2-docker.pkg.dev/gds-social-data/cloud-run-source-deploy/bq-youtube-short-or-long@sha256:2cae90ffc7190ec5b15d031eca85d6ebc85a00c8f78a24c2f0064c65314eb9c5
- Base image
europe-west2-docker.pkg.dev/serverless-runtimes/google-24/runtimes/python314
- Base image update: Automatic
- Port: 8080

Build
- CPU limit: 1
- Memory limit: 512MiB

- requirements.txt
functions-framework==3.*
requests==2.31.0

```python
import functions_framework
import requests
from concurrent.futures import ThreadPoolExecutor

# Global session for connection pooling across warm starts
session = requests.Session()
# Increase the pool size to match our worker count
adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50)
session.mount('https://', adapter)

def check_video(video_id):
    if not video_id:
        return None

    url = f"https://www.youtube.com/shorts/{video_id}"
    try:
        # HEAD request with a strict timeout to prevent one slow video from
        # hanging the whole BigQuery batch
        response = session.head(url, allow_redirects=False, timeout=3.5)

        if response.status_code == 303:
            return "Long"
        elif response.status_code == 200:
            return "Short"
        else:
            return f"Unknown ({response.status_code})"
    except Exception:
        return "Error"

@functions_framework.http
def youtube_type_checker(request):
    request_json = request.get_json(silent=True)
    calls = request_json.get('calls', [])

    # Process exactly the batch BigQuery sent (ideally 50)
    with ThreadPoolExecutor(max_workers=50) as executor:
        # Maintain order for BigQuery
        replies = list(executor.map(lambda x: check_video(x[0]), calls))

    return {"replies": replies}

```
