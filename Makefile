PROJECT      = gds-social-data
LOCATION     = europe-west2
COMPOSER_ENV = social-ingestion-composer
COMPOSER_SA  = composer-social-ingestion@$(PROJECT).iam.gserviceaccount.com
AR_REPO      = gds-social-data-composer-artifacts

DAG_IDS = facebook_ingestion instagram_ingestion youtube_ingestion

COMPOSER_ENV_TIMEOUT_SECS = 2400
COMPOSER_ENV_POLL_SECS    = 60
DAG_RUN_TIMEOUT_SECS      = 7200
DAG_RUN_POLL_SECS         = 300

.PHONY: help \
	setup create-artifact-registry create-composer-sa \
	build \
	buildx-setup test-local-pyarrow test-local-container \
	create-composer-env wait-composer-env-ready \
	deploy-dags \
	trigger-daily-runs \
	poll-daily-runs \
	delete-composer-env \
	daily-cycle

help:
	@echo "One-time setup (run once per GCP project):"
	@echo "  make setup                    Run create-artifact-registry and create-composer-sa"
	@echo "  make create-artifact-registry  Create the Artifact Registry Docker repo"
	@echo "  make create-composer-sa       Create the Composer service account and grant IAM roles"
	@echo ""
	@echo "Manual (run by hand whenever pipeline code changes):"
	@echo "  make build                    Build and push the pipeline image via Cloud Build"
	@echo ""
	@echo "Local testing:"
	@echo "  make buildx-setup             Create the multi-platform-builder buildx builder (idempotent)"
	@echo "  make test-local-pyarrow       Build linux/amd64 image locally and smoke-test pyarrow import"
	@echo "  make test-local-container     Build linux/amd64 image locally and enter"
	@echo ""
	@echo "Daily cycle (intended to run on a nightly schedule, e.g. cron or GH Actions):"
	@echo "  make create-composer-env      [02:00] Start creating the Composer environment (async, returns immediately)"
	@echo "  make wait-composer-env-ready  [02:00] Poll until RUNNING (~30 min, budget $(COMPOSER_ENV_TIMEOUT_SECS)s)"
	@echo "  make deploy-dags              Sync dags/ into the freshly created environment"
	@echo "  make trigger-daily-runs       [03:00] Manually trigger the 3 ingestion DAGs (they also fire on their own schedule)"
	@echo "  make poll-daily-runs          Poll all 3 DAG runs until each reaches a terminal state"
	@echo "  make delete-composer-env      Delete the Composer environment"
	@echo "  make daily-cycle              Run the full nightly sequence above in order"

# ---------------------------------------------------------------------------
# One-time setup: prerequisites (AR repo, Composer service account/IAM).
# Not part of the nightly cycle - the environment created below reuses these.
# ---------------------------------------------------------------------------

setup: create-artifact-registry create-composer-sa

create-artifact-registry:
	gcloud artifacts repositories create $(AR_REPO) \
		--repository-format=docker \
		--location=$(LOCATION) \
		--description="Social media ingestion pipeline images" \
		--project=$(PROJECT)

create-composer-sa:
	if ! gcloud iam service-accounts describe $(COMPOSER_SA) --project=$(PROJECT) > /dev/null 2>&1; then \
		gcloud iam service-accounts create composer-social-ingestion \
			--display-name="Composer Social Media Ingestion" \
			--project=$(PROJECT) && sleep 10; \
	fi
	for role in \
		roles/composer.worker \
		roles/logging.logWriter \
		roles/monitoring.metricWriter \
		roles/monitoring.viewer \
		roles/secretmanager.secretAccessor \
		roles/bigquery.dataEditor \
		roles/bigquery.jobUser \
		roles/artifactregistry.reader; do \
		gcloud projects add-iam-policy-binding $(PROJECT) \
			--member="serviceAccount:$(COMPOSER_SA)" \
			--role="$$role" || exit 1; \
	done

# ---------------------------------------------------------------------------
# Manual: rebuild the pipeline image whenever src/facebook/instagram code
# changes. Future GH Actions equivalent: a workflow_dispatch-triggered job,
# not a cron.
# ---------------------------------------------------------------------------

build:
	gcloud builds submit \
		--config=cloudbuild.yaml \
		--substitutions="_COMMIT_SHA=$$(git rev-parse HEAD)" \
		--project=$(PROJECT) \
		.

# ---------------------------------------------------------------------------
# Local testing
# ---------------------------------------------------------------------------

buildx-setup:
	docker buildx inspect multi-platform-builder > /dev/null 2>&1 || \
		docker buildx create --name multi-platform-builder

test-local-pyarrow: buildx-setup
	docker buildx build --builder multi-platform-builder \
		--no-cache --platform linux/amd64 \
		-t pipeline:local --load .
	docker run --rm pipeline:local -c \
		"import pyarrow; print('pyarrow OK:', pyarrow.__version__)"

test-local-container: buildx-setup
	docker buildx build --builder multi-platform-builder \
		--no-cache --platform linux/amd64 \
		-t pipeline:local --load .
	docker run --rm -it \
	--name social-ingestion-debug \
	--entrypoint /bin/bash \
	-v ~/.config/gcloud:/root/.config/gcloud:ro \
	-e GOOGLE_CLOUD_PROJECT=gds-social-data \
	-e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
	pipeline:local

# ---------------------------------------------------------------------------
# Daily [02:00]: create the Composer environment.
# Future GH Actions equivalent: a job on cron "0 2 * * *".
# ---------------------------------------------------------------------------

create-composer-env:
	if gcloud composer environments describe $(COMPOSER_ENV) --location=$(LOCATION) --project=$(PROJECT) > /dev/null 2>&1; then \
		echo "$(COMPOSER_ENV) already exists, skipping create"; \
	else \
		gcloud composer environments create $(COMPOSER_ENV) \
			--location=$(LOCATION) \
			--project=$(PROJECT) \
			--service-account=$(COMPOSER_SA) \
			--image-version=composer-3-airflow-2.10 \
			--async; \
	fi

wait-composer-env-ready:
	@echo "Waiting for $(COMPOSER_ENV) to reach RUNNING (timeout $(COMPOSER_ENV_TIMEOUT_SECS)s)..."
	@elapsed=0; \
	while true; do \
		state=$$(gcloud composer environments describe $(COMPOSER_ENV) --location=$(LOCATION) --project=$(PROJECT) --format='value(state)'); \
		echo "  state=$$state elapsed=$${elapsed}s"; \
		if [ "$$state" = "RUNNING" ]; then echo "$(COMPOSER_ENV) is ready."; exit 0; fi; \
		if [ "$$state" = "ERROR" ]; then echo "$(COMPOSER_ENV) entered ERROR state" >&2; exit 1; fi; \
		if [ $$elapsed -ge $(COMPOSER_ENV_TIMEOUT_SECS) ]; then echo "Timed out waiting for $(COMPOSER_ENV)" >&2; exit 1; fi; \
		sleep $(COMPOSER_ENV_POLL_SECS); elapsed=$$((elapsed + $(COMPOSER_ENV_POLL_SECS))); \
	done

# ---------------------------------------------------------------------------
# Daily: sync dags/ into the freshly created environment.
# Runs after wait-composer-env-ready. Future GH Actions equivalent: next job,
# `needs: [wait-composer-env-ready]`.
# ---------------------------------------------------------------------------

deploy-dags:
	gcloud composer environments storage dags import \
		--environment=$(COMPOSER_ENV) \
		--location=$(LOCATION) \
		--project=$(PROJECT) \
		--source=dags/

# ---------------------------------------------------------------------------
# Daily [03:00]: facebook_ingestion, instagram_ingestion and youtube_ingestion
# all fire on their own "0 3 * * *" schedule once the DAGs are deployed and
# the environment is RUNNING. This target is only for manual/on-demand or
# backfill use - it is not needed in the normal nightly path.
# ---------------------------------------------------------------------------

trigger-daily-runs:
	for dag_id in $(DAG_IDS); do \
		gcloud composer environments run $(COMPOSER_ENV) \
			--location=$(LOCATION) \
			--project=$(PROJECT) \
			-- dags trigger $$dag_id || exit 1; \
	done

# ---------------------------------------------------------------------------
# Daily: poll all 3 DAGs until each reaches a terminal state. This is the gate
# before delete-composer-env - the environment must not be deleted mid-run.
# ---------------------------------------------------------------------------

poll-daily-runs:
	for dag_id in $(DAG_IDS); do \
		echo "Polling $$dag_id (timeout $(DAG_RUN_TIMEOUT_SECS)s)..."; \
		elapsed=0; \
		while true; do \
			state=$$(gcloud composer environments run $(COMPOSER_ENV) \
				--location=$(LOCATION) --project=$(PROJECT) \
				-- dags list-runs -d $$dag_id --output json 2>/dev/null \
				| python3 -c "import json,sys; runs=json.load(sys.stdin); print(runs[0]['state'] if runs else 'none')"); \
			echo "  $$dag_id state=$$state elapsed=$${elapsed}s"; \
			if [ "$$state" = "success" ]; then break; fi; \
			if [ "$$state" = "failed" ]; then echo "$$dag_id failed" >&2; exit 1; fi; \
			if [ $$elapsed -ge $(DAG_RUN_TIMEOUT_SECS) ]; then echo "Timed out waiting for $$dag_id" >&2; exit 1; fi; \
			sleep $(DAG_RUN_POLL_SECS); elapsed=$$((elapsed + $(DAG_RUN_POLL_SECS))); \
		done; \
	done

# ---------------------------------------------------------------------------
# Daily: tear down the environment once all 3 runs have completed.
# ---------------------------------------------------------------------------

delete-composer-env:
	gcloud composer environments delete $(COMPOSER_ENV) \
		--location=$(LOCATION) \
		--project=$(PROJECT) \
		--quiet

daily-cycle: create-composer-env wait-composer-env-ready deploy-dags poll-daily-runs delete-composer-env
