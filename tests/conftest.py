import shutil

import pytest

from engine import credibility


@pytest.fixture(autouse=True)
def isolated_experience(tmp_path, monkeypatch):
    """Tests read the seed portfolio but never write to the real experience store."""
    path = tmp_path / "redteam_experience.jsonl"
    shutil.copy(credibility.EXPERIENCE_PATH, path)
    monkeypatch.setattr(credibility, "EXPERIENCE_PATH", path)
    real_record = credibility.record
    monkeypatch.setattr(credibility, "record", lambda *a, **kw: real_record(*a, **{**kw, "path": path}))
    yield path
