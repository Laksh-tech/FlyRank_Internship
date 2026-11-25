import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    db.ensure_plans(c)
    db.create_tenant(c, "t_alpha", "Alpha", "key-alpha")
    db.create_tenant(c, "t_beta", "Beta", "key-beta")
    yield c
    c.close()
