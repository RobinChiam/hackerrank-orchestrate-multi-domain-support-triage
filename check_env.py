from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple


REPO_ROOT = Path(__file__).resolve().parent
ENV_FILE = REPO_ROOT / ".env"
CODE_DIR = REPO_ROOT / "code"
DATA_DIR = REPO_ROOT / "data"
SUPPORT_TICKETS_DIR = REPO_ROOT / "support_tickets"
INDEX_DIR = CODE_DIR / ".triage_index"


class CheckResult(NamedTuple):
    name: str
    ok: bool
    detail: str


def _load_env_values(path: Path) -> dict[str, str]:
    """Load .env values with python-dotenv when available, else use a small fallback parser."""
    if not path.exists():
        return {}

    try:
        from dotenv import dotenv_values
    except ImportError:
        dotenv_values = None

    if dotenv_values is not None:
        loaded = dotenv_values(path)
        return {
            str(key): "" if value is None else str(value).strip()
            for key, value in loaded.items()
            if key is not None
        }

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def _ensure_directories() -> list[Path]:
    """Create any setup-managed directories that are missing."""
    created: list[Path] = []
    for path in (INDEX_DIR,):
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
    return created


def _write_or_update_env_key(path: Path, key_name: str, key_value: str) -> None:
    """Create or update one key inside the repo .env file without disturbing other entries."""
    new_line = f"{key_name}={key_value}"
    if not path.exists():
        path.write_text(f"{new_line}\n", encoding="utf-8")
        return

    updated_lines: list[str] = []
    replaced = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in raw_line:
            current_key, _current_value = raw_line.split("=", 1)
            if current_key.strip() == key_name:
                updated_lines.append(new_line)
                replaced = True
                continue
        updated_lines.append(raw_line)

    if not replaced:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.append(new_line)

    path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


def ensure_env_key(*, path: Path, key_name: str = "GEMINI_API_KEY") -> bool:
    """Prompt for GEMINI_API_KEY when it is missing or empty."""
    values = _load_env_values(path)
    current_value = values.get(key_name, "").strip()
    if current_value:
        return False

    try:
        entered_value = input(f"Enter {key_name}: ").strip()
    except EOFError as exc:
        raise RuntimeError(
            f"{key_name} is missing and no interactive input is available to create {path.name}."
        ) from exc

    if not entered_value:
        raise RuntimeError(f"{key_name} cannot be empty.")

    _write_or_update_env_key(path, key_name, entered_value)
    return True


def run_checks() -> list[CheckResult]:
    """Run environment checks and return the detailed results."""
    env_values = _load_env_values(ENV_FILE)
    gemini_api_key = env_values.get("GEMINI_API_KEY", "").strip()

    checks = [
        CheckResult(
            "Python version",
            sys.version_info >= (3, 11),
            f"Detected {sys.version.split()[0]}",
        ),
        CheckResult(
            ".env file",
            ENV_FILE.exists(),
            f"Expected at {ENV_FILE}",
        ),
        CheckResult(
            "GEMINI_API_KEY",
            bool(gemini_api_key),
            "Present in .env" if gemini_api_key else "Missing or empty in .env",
        ),
    ]

    required_paths = [
        ("code directory", CODE_DIR),
        ("data directory", DATA_DIR),
        ("support_tickets directory", SUPPORT_TICKETS_DIR),
        ("triage index directory", INDEX_DIR),
    ]
    for label, path in required_paths:
        checks.append(
            CheckResult(
                label,
                path.exists(),
                str(path),
            )
        )

    return checks


def print_results(results: list[CheckResult]) -> None:
    """Print a human-readable summary of the environment checks."""
    for result in results:
        marker = "PASS" if result.ok else "FAIL"
        print(f"[{marker}] {result.name}: {result.detail}")


def main(argv: list[str] | None = None) -> int:
    """Validate the local development environment for this project."""
    parser = argparse.ArgumentParser(
        description="Validate the local environment for the Support Triage Agent."
    )
    parser.add_argument(
        "--ensure-env",
        action="store_true",
        help="Prompt for GEMINI_API_KEY and create/update .env when needed.",
    )
    parser.add_argument(
        "--create-dirs",
        action="store_true",
        help="Create setup-managed directories when missing.",
    )
    args = parser.parse_args(argv)

    if args.create_dirs:
        created = _ensure_directories()
        for path in created:
            print(f"[INFO] Created directory: {path}")

    if args.ensure_env:
        created_or_updated = ensure_env_key(path=ENV_FILE)
        if created_or_updated:
            print(f"[INFO] Updated environment file: {ENV_FILE}")

    results = run_checks()
    print_results(results)

    failed = [result for result in results if not result.ok]
    if failed:
        print("\nEnvironment validation failed.")
        print("Run `make setup` to bootstrap the project, then re-run `python3 check_env.py`.")
        return 1

    print("\nEnvironment looks good.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
