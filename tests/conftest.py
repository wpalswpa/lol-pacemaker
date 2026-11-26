import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never read/write the real ~/.lol-pacemaker during tests."""
    monkeypatch.setenv("LOL_PACEMAKER_HOME", str(tmp_path / "home"))
