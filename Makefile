.PHONY: test lint format check install dev

test:
	.venv/bin/python -m pytest tests/ -x -q

test-verbose:
	.venv/bin/python -m pytest tests/ -v

lint:
	.venv/bin/ruff check emploi/ tests/

format:
	.venv/bin/ruff format emploi/ tests/
	.venv/bin/ruff check --fix emploi/ tests/

check: lint test

install:
	pip install -e .

dev:
	pip install -e ".[dev]"
	pip install ruff mypy pre-commit
