# Makefile for running typical developer workflow actions.

# Parse python version from pyproject.toml (`requires-python` line)
PYTHON_VERSION := $(shell grep "requires-python = " pyproject.toml | sed 's/[^0-9.]*//g')
UV_VERSION := $(shell ./setup/extract_uv_version.sh pyproject.toml)

# `uv run <console-script>` does not put the working directory on sys.path the
# way `python -m` does, so `from pdftools import ...` fails without this.
# Prepended rather than assigned, so an existing PYTHONPATH is not clobbered.
export PYTHONPATH := $(CURDIR)$(if $(PYTHONPATH),:$(PYTHONPATH),)

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "make setup            Create the venv and install dependencies"
	@echo "make local-env-check  Verify uv, Python, Ghostscript and poppler are present"
	@echo "make dev              Run the server on http://127.0.0.1:5057"
	@echo "make lint             ruff check, ruff format and ty check"
	@echo "make test             Run the test suite"

# Check uv + Python toolchain + the external binaries the toolbox shells out to.
.PHONY: local-env-check
local-env-check:
	./setup/local_env_check.sh $(PYTHON_VERSION) $(UV_VERSION)

# Update uv to the required version.
.PHONY: update-uv
update-uv:
# Do not change the version if running inside Github Actions.
ifndef IS_CI_BUILD
	curl -LsSf https://astral.sh/uv/$(UV_VERSION)/install.sh | sh;
endif

# Create a virtual environment (if it doesn't exist) with the correct python version.
.PHONY: create-venv
create-venv: update-uv
	uv venv --python=$(PYTHON_VERSION)

# Install all dependencies.
.PHONY: install-deps
install-deps: update-uv
	uv sync

# One command to go from a fresh checkout to a working environment.
.PHONY: setup
setup: create-venv install-deps

# Run the PDF Toolbox server locally. Override the port with PDFTOOLBOX_PORT.
.PHONY: dev
dev:
	uv run python app.py

# Lint the codebase using ruff, and type-check it using ty.
# Deliberately does not depend on update-uv: re-downloading uv on every lint run
# makes the fast feedback loop slow for no benefit.
.PHONY: lint
lint:
	uv run ruff check $(if ${IS_CI_BUILD},--output-format=github,--fix) .
	uv run ruff format $(if ${IS_CI_BUILD},--check,) .
	uv run ty check

# Run unit tests.
.PHONY: test
test:
	uv run pytest tests/ -v
