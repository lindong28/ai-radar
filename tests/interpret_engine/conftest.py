import pytest

from airadar.interpret.engine import embedding, tag_normalizer


@pytest.fixture(autouse=True)
def isolate_engine_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path / "kb")
    monkeypatch.setenv("AI_RADAR_KB_ROOT", str(tmp_path / "kb"))
    monkeypatch.delenv("AI_ASSISTANT_ROOT", raising=False)
    monkeypatch.delenv("AI_RADAR_INTERPRET_TAGS_PATH", raising=False)
    tag_normalizer._VECTOR_CACHE.clear()
