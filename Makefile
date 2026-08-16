.PHONY: test lint format check cov install dev

test:
	.venv/bin/python -m pytest tests/ -x -q

test-verbose:
	.venv/bin/python -m pytest tests/ -v

cov:
	.venv/bin/python -m pytest tests/ --cov=emploi --cov-report=term-missing

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
