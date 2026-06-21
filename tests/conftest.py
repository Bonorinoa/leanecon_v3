from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture(scope="session")
def warm_lean_workspace() -> dict[str, object]:
    """Hydrate Lean artifacts before tests that assert on Lean compilation."""

    from src.lean.compiler import lean_workspace_warm

    result = lean_workspace_warm(timeout=180)
    assert result.get("success"), result
    return result
