import pytest

from casadata.db import connect_memory
from casadata.geo.casablanca import sync_locations


@pytest.fixture()
def conn():
    c = connect_memory()
    sync_locations(c)
    yield c
    c.close()
