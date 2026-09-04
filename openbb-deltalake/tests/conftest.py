"""Test fixtures: temporary local Delta store per test."""

import pytest


@pytest.fixture
def store(tmp_path):
    """Return a DeltaStore pointed at a fresh temporary directory."""
    from openbb_deltalake.store import DeltaStore

    return DeltaStore(uri=str(tmp_path), library="test")
