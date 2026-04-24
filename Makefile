.PHONY: dev test typecheck install check

install:
	pip install -e ".[dev]"

dev:
	uvicorn main:app --reload --host 127.0.0.1 --port 8000

test:
	pytest -v

typecheck:
	mypy main.py tests/

check: typecheck test
	@echo "All checks passed!"
