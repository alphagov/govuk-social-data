# Set up 
1) Ensure you are part of the gcp-data-infrastructure-editors@digital.cabinet-office.gov.uk

2) Sensure you have set you local gcloud config to gds-social-data project. you can set and check with `gcloud config get project` and `gcloud config set project gds-social-data`

3) Run `gcloud auth application-default login` to ensure you have local credentials for the scripts to execute locally

4) Set up your local poetry env 
```
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
pyenv virtualenv 3.14.3 <environment_name>
pyenv local <environment_name>

# 2. Initialise Poetry in the project (skip if pyproject.toml already exists)
poetry init

# 3. Configure Poetry to create the .venv inside the project directory
poetry config virtualenvs.in-project true

# 4. Create the virtualenv using the pyenv-managed Python
poetry env use $(pyenv which python)

# 5. Install dependencies
poetry install
```


5) Check the target datasets exist in project gds-social-data
 - govuk_facebook
 - govuk_facebook_dev
 - govuk_instagram 
 - govuk_instagram_dev  
 - govuk_youtube_channel

6) test manual hooks 
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

## Authentication with google groups 

Permissions are granted gcp-data-infrastructure-editors@digital.cabinet-office.gov.uk

This gives the following permissions on the dataform custom service account custom-dataform-service-accoun@gds-social-data.iam.gserviceaccount.com
- Service account User on the custom-dataform-service account 
- Service account ActAs permissions 

For the project gds-social-data it gives editor, which includes the rquired permissions bellow: 
| roles/bigquery.dataEditor	          | Enables writing of rows to bigquery when running locally |
| roles/bigquery.jobUser              |	Required to submit BigQuery load jobs
| roles/secretmanager.secretAccessor  | Reads the 6 API secrets from Secret Manager |