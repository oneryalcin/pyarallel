.PHONY: help test docs-serve docs-deploy format lint clean build publish

help:
	@echo "Available commands:"
	@echo "  make test         Run pytest suite"
	@echo "  make docs-serve   Start mkdocs development server"
	@echo "  make docs-deploy  Deploy documentation to GitHub Pages"
	@echo "  make format      Format code with ruff"
	@echo "  make lint        Run ruff check + mypy"
	@echo "  make clean       Remove build artifacts"
	@echo "  make build       Build package"
	@echo "  make publish     Publish package to PyPI"

test:
	uv run pytest tests/ -v

docs-serve:
	uv run mkdocs serve

docs-deploy:
	uv run python -c "import pyarallel; from pathlib import Path; yml = Path('mkdocs.yml').read_text(); Path('mkdocs.yml').write_text(yml.replace('version: .*', f'version: {pyarallel.__version__}'))"
	uv run mkdocs gh-deploy

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .
	uv run mypy pyarallel/

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete
	find . -type f -name '*.pyd' -delete

build:
	$(MAKE) clean
	uv run python -m build

publish: build
	uv run twine upload dist/*