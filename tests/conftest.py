import pytest

from engine import credibility


@pytest.fixture(autouse=True)
def isolated_experience(tmp_path, monkeypatch):
    """Tests use only the fixed FICTIONAL seed portfolio (so results don't drift as
    real underwriting runs join the store) and never write to the real store."""
    path = tmp_path / "redteam_experience.jsonl"
    seed = [l for l in credibility.EXPERIENCE_PATH.read_text().splitlines() if "FICTIONAL seed portfolio" in l]
    path.write_text("".join(l + "\n" for l in seed))
    monkeypatch.setattr(credibility, "EXPERIENCE_PATH", path)
    real_record = credibility.record
    monkeypatch.setattr(credibility, "record", lambda *a, **kw: real_record(*a, **{**kw, "path": path}))
    yield path
