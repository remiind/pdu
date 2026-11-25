import pytest


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    """Tests laufen ohne Hintergrund-Aktualisierung (außer sie schalten sie gezielt ein)."""
    monkeypatch.setenv("PDU_BACKGROUND", "0")
