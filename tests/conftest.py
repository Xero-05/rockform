import pytest


@pytest.fixture(autouse=True)
def isolated_hold_library(tmp_path_factory, monkeypatch):
    """Never read or change the user's own hold library: each test gets an empty one."""
    monkeypatch.setenv("ROCKFORM_HOLD_LIBRARY", str(tmp_path_factory.mktemp("hold-library")))
