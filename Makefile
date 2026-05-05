SHELL := /bin/bash

PYTHON ?= python3
VENV_DIR ?= venv
VENV_PYTHON := $(VENV_DIR)/bin/python
VENV_PIP := $(VENV_DIR)/bin/pip

.PHONY: setup check-env clean

setup:
	@$(PYTHON) -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 'Python 3.11+ is required for this project.')"
	@test -d $(VENV_DIR) || $(PYTHON) -m venv $(VENV_DIR)
	@$(VENV_PYTHON) -m pip install --upgrade pip
	@$(VENV_PIP) install -r requirements.txt
	@$(VENV_PYTHON) check_env.py --create-dirs --ensure-env
	@echo
	@echo "Setup complete."
	@echo "Next steps:"
	@echo "  source $(VENV_DIR)/bin/activate"
	@echo "  python3 code/main.py --help"
	@echo "  python3 code/tui.py"

check-env:
	@if [ -x "$(VENV_PYTHON)" ]; then \
		$(VENV_PYTHON) check_env.py; \
	else \
		$(PYTHON) check_env.py; \
	fi

clean:
	rm -rf $(VENV_DIR)
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
