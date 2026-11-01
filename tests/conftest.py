import pytest

from tests.helpers.repo import make_repo


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "r")
