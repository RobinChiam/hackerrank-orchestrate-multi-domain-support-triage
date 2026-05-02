from __future__ import annotations

import os
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODE_DIR.parent
DATA_DIR = REPO_ROOT / "data"
SUPPORT_TICKETS_DIR = REPO_ROOT / "support_tickets"
DEFAULT_INPUT_CSV = SUPPORT_TICKETS_DIR / "support_tickets.csv"
DEFAULT_OUTPUT_CSV = SUPPORT_TICKETS_DIR / "output.csv"
ENV_FILE = REPO_ROOT / ".env"
TRIAGE_SCHEMA_PATH = CODE_DIR / "response_schema"
INDEX_DIR = CODE_DIR / ".triage_index"
INDEX_DB_PATH = INDEX_DIR / "support_corpus.sqlite3"

DEFAULT_TRIAGE_MODEL = "gemini-3.1-flash-lite-preview"
DEFAULT_RESPONSE_MODEL = "gemini-3.1-flash-lite-preview"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-2"
DEFAULT_EMBEDDING_DIMENSION = 768


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        values[key] = value
        os.environ.setdefault(key, value)
    return values


def resolve_gemini_api_key() -> str:
    load_env_file()
    for env_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    raise RuntimeError(
        "Missing Gemini API credentials. Set GEMINI_API_KEY or GOOGLE_API_KEY in the repo .env file."
    )


def resolve_model(env_name: str, default: str) -> str:
    load_env_file()
    return os.environ.get(env_name, default).strip() or default
