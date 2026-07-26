"""Environment configuration utilities.

Loads optional .env files so local development does not require shell-level
exports. Existing process environment variables always take precedence.
"""
from pathlib import Path

from dotenv import load_dotenv


def load_environment() -> None:
    app_dir = Path(__file__).resolve().parent
    backend_dir = app_dir.parent
    project_dir = backend_dir.parent

    # Load root and backend env files; explicit shell env vars still win.
    load_dotenv(project_dir / ".env", override=False)
    load_dotenv(backend_dir / ".env", override=False)
