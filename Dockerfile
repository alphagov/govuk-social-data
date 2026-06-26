# hadolint ignore=DL3029
FROM --platform=linux/amd64 python:3.14.3-slim AS builder
RUN apt-get update && \
    apt-get install --no-install-suggests --no-install-recommends --yes pipx
ENV PATH="/root/.local/bin:${PATH}"
RUN pipx install poetry==2.3.2
RUN pipx inject poetry poetry-plugin-export
WORKDIR /src
COPY pyproject.toml poetry.lock ./
RUN poetry export --without dev --without-hashes --output /requirements.txt

# hadolint ignore=DL3029
FROM --platform=linux/amd64 python:3.14.3-slim
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY facebook/ ./facebook/
COPY instagram/ ./instagram/
ENTRYPOINT ["python"]
