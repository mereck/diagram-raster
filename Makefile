.PHONY: check fmt install

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check
	uv run pytest

fmt:
	uv run ruff check --fix .
	uv run ruff format .

install:
	uv tool install --editable . --force
