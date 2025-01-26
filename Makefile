.PHONY: help test docs-serve docs-deploy format lint clean

help:
	@echo "Available commands:"
	@echo "  make test         Run pytest suite"
	@echo "  make docs-serve   Start mkdocs development server"
	@echo "  make docs-deploy  Deploy documentation to GitHub Pages"
	@echo "  make format      Format code with black and isort"
	@echo "  make lint        Run mypy for type checking"
	@echo "  make clean       Remove build artifacts"

test:
	pytest tests/ -v

docs-serve:
	mkdocs serve

docs-deploy:
	python -c "import pyarallel; from pathlib import Path; yml = Path('mkdocs.yml').read_text(); Path('mkdocs.yml').write_text(yml.replace('version: .*', f'version: {pyarallel.__version__}'))"
	mkdocs gh-deploy

format:
	black .
	isort .

lint:
	mypy pyarallel/

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete
	find . -type f -name '*.pyd' -delete