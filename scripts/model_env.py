"""Explicit local credential loading for launchers only; providers use environment."""
import os
from pathlib import Path


def load_credentials_file(path):
    if not os.environ.get("MODEL_API_KEY") and path is not None:
        value = Path(path).read_text(encoding="utf-8").strip()
        if not value or any(c.isspace() for c in value):
            raise ValueError("Credential file must contain one nonempty API key")
        os.environ["MODEL_API_KEY"] = value
    return bool(os.environ.get("MODEL_API_KEY"))
