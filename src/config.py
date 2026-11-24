"""Application limits and local service configuration."""
import os
from urllib.parse import urlsplit

MAX_BYTES = 50 * 1024 * 1024
MAX_PAGES = 300
MISSING = "Not specified in the source document."
COLUMNS = ("Activity / Deliverable", "Submission Deadline", "Duration", "Milestone / Expected Output")


def ollama_settings() -> tuple[str, str, float]:
    url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    parts = urlsplit(url)
    if (parts.scheme not in {"http", "https"} or parts.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parts.username or parts.password or parts.path or parts.query or parts.fragment):
        raise ValueError("OLLAMA_BASE_URL must be a loopback HTTP(S) origin.")
    timeout = float(os.getenv("OLLAMA_TIMEOUT", "120"))
    if not 0 < timeout <= 600:
        raise ValueError("OLLAMA_TIMEOUT must be between 0 and 600 seconds.")
    return url, os.getenv("OLLAMA_MODEL", "qwen3:1.7b"), timeout
