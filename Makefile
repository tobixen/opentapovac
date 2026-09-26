.PHONY: help install dev lint format test clean venv-install

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## Install the package (auto-detects uv, pipx, or pip; system-wide as root)
	@if [ "$$(id -u)" = "0" ]; then \
		echo "Running as root, installing system-wide (commands in /usr/local/bin)..."; \
		export UV_TOOL_DIR=/opt/uv/tools UV_TOOL_BIN_DIR=/usr/local/bin \
			UV_PYTHON_INSTALL_DIR=/opt/uv/python \
			PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin \
			PIPX_MAN_DIR=/usr/local/share/man; \
	fi; \
	if command -v uv >/dev/null 2>&1; then \
		echo "Installing with uv..."; \
		uv tool install --reinstall --force .; \
	elif command -v pipx >/dev/null 2>&1; then \
		echo "Installing with pipx..."; \
		pipx install --force .; \
	elif [ "$$(id -u)" = "0" ]; then \
		echo "Neither uv nor pipx found, trying a plain pip install."; \
		echo "On PEP 668 distros (Arch, Debian 12+) this is refused - install uv or pipx instead."; \
		pip install .; \
	else \
		echo "Tip: Install uv or pipx for isolated installs (pacman -S uv, apt install pipx, brew install uv)"; \
		echo "Falling back to pip install --user ..."; \
		PIP_BREAK_SYSTEM_PACKAGES=1 pip install --user .; \
	fi

dev:  ## Install with dev dependencies (editable)
	@if [ "$$(id -u)" = "0" ]; then echo "Refusing to run make dev as root"; exit 1; fi
	PIP_BREAK_SYSTEM_PACKAGES=1 pip install -e ".[dev]"

lint:  ## Run ruff linter and formatter check
	python -m ruff check src/ tests/ hatch_build.py
	python -m ruff format --check src/ tests/ hatch_build.py

format:  ## Auto-format code
	python -m ruff check --fix src/ tests/ hatch_build.py
	python -m ruff format src/ tests/ hatch_build.py

test:  ## Run tests
	python -m pytest

clean:  ## Remove build artifacts
	rm -rf dist/ build/ *.egg-info src/*.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

VENV := .venv
WRAPPER_DIR := $(HOME)/bin

venv-install:  ## Install into a venv and create ~/bin wrapper
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install .
	mkdir -p $(WRAPPER_DIR)
	@printf '#!/bin/sh\nexec %s/bin/opentapovac "$$@"\n' "$$(pwd)/$(VENV)" > $(WRAPPER_DIR)/opentapovac
	chmod +x $(WRAPPER_DIR)/opentapovac
	@echo "Installed: $(WRAPPER_DIR)/opentapovac -> $$(pwd)/$(VENV)/bin/opentapovac"
