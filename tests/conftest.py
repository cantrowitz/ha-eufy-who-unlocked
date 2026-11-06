"""Shared test helpers.

``lock_push.py`` is stdlib-only, so it is loaded by file path —
importing the integration package would pull in Home Assistant,
which is not installed on this VM.
"""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LOCK_PUSH_PATH = REPO / "custom_components" / "eufy_who_unlocked" / "lock_push.py"


def load_lock_push():
    """Load lock_push.py as a standalone module (no package import)."""
    spec = importlib.util.spec_from_file_location("eufy_lock_push", LOCK_PUSH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def lock_push():
    """The lock_push module, loaded once per test session."""
    return load_lock_push()
