install:
	uv sync

unit-tests:
	uv run pytest tests/ -m "not integration" -q --tb=short

integration-tests:
	uv run pytest tests/ -m "integration" -q --tb=short
