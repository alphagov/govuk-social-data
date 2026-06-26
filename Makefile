PROJECT      = gds-social-data
LOCATION     = europe-west2
COMPOSER_ENV = social-ingestion-composer
COMPOSER_SA  = composer-social-ingestion@$(PROJECT).iam.gserviceaccount.com
AR_REPO      = gds-social-data-composer-artifacts

.PHONY: build test-local buildx-setup deploy-dags setup create-artifact-registry create-composer-sa create-composer-env help

help:
	@echo "Setup (run once):"
	@echo "  make setup                   Run create-artifact-registry and create-composer-sa"
	@echo "  make create-artifact-registry Create the Artifact Registry Docker repo"
	@echo "  make create-composer-sa      Create the Composer service account and grant IAM roles"
	@echo "  make create-composer-env     Create the Composer environment (~20 min)"
	@echo ""
	@echo "Local testing:"
	@echo "  make buildx-setup            Create the multi-platform-builder buildx builder (idempotent)"
	@echo "  make test-local-pyarrow      Build linux/amd64 image locally and smoke-test pyarrow import"
	@echo "  make test-local-container    Build linux/amd64 image locally and enter"
	@echo ""
	@echo "Deploy:"
	@echo "  make build                   Build and push the pipeline image via Cloud Build"
	@echo "  make deploy-dags             Deploy DAG files to the Composer environment"

setup: create-artifact-registry create-composer-sa create-composer-env

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

create-composer-env:
	gcloud composer environments create $(COMPOSER_ENV) \
		--location=$(LOCATION) \
		--project=$(PROJECT) \
		--service-account=$(COMPOSER_SA) \
		--image-version=composer-3-airflow-2.10

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

build:
	gcloud builds submit \
		--config=cloudbuild.yaml \
		--substitutions="_COMMIT_SHA=$$(git rev-parse HEAD)" \
		--project=$(PROJECT) \
		.

deploy-dags:
	gcloud composer environments storage dags import \
		--environment=$(COMPOSER_ENV) \
		--location=$(LOCATION) \
		--project=$(PROJECT) \
		--source=dags/
