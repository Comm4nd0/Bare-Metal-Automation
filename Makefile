.PHONY: all lint format typecheck test clean

all: lint typecheck test

lint:
	ruff check src/

format:
	ruff format src/

typecheck:
	mypy src/

test:
	pytest tests/ -v

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .mypy_cache .pytest_cache
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
