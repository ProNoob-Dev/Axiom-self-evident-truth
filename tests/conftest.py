import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.graph import Axiom
from app.main import create_app
from scripts.seed import seed


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "billing.sqlite"
    seed(path)
    return str(path)


@pytest.fixture()
def client(db_path, tmp_path):
    axiom = Axiom(db_path=db_path, audit_path=str(tmp_path / "audit.log"))
    return TestClient(create_app(axiom))


@pytest.fixture()
def axiom(db_path, tmp_path):
    return Axiom(db_path=db_path, audit_path=str(tmp_path / "audit.log"))
