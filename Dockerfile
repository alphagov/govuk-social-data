FROM python:3.14.3-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY . .


FROM python:3.14.3-slim

RUN groupadd --gid 999 app && \
    useradd --system --create-home --uid 999 --gid app app

WORKDIR /app

COPY --chown=app:app --from=build /app /app

ENV PATH="/app/.venv/bin:$PATH"

USER app

ENTRYPOINT ["python"]
