from __future__ import annotations

import os
from pathlib import Path


def default_database_path() -> Path:
    """Return the active MSBTS badminton database path."""
    configured = os.environ.get("MSBTS_DATABASE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".msbts" / "msbts.db"
