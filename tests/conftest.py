import pytest


@pytest.fixture(autouse=True)
def disable_openclaw_observer_by_default(monkeypatch):
    monkeypatch.setenv("CLAUDE_OPENCLAW_OBSERVER_ENABLED", "false")
