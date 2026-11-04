import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from app import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()
