import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.getenv("SOCIAL_PERSONA_RUN_LIVE") == "1" or os.getenv("LIVE_PROFILE_TESTS") == "1":
        return

    skip_live = pytest.mark.skip(reason="set SOCIAL_PERSONA_RUN_LIVE=1 to run live profile smoke tests")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
