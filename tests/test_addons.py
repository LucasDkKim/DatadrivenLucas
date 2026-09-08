import json

from clipo_cs_bot.context import filter_channels_by_keyword
from clipo_cs_bot.manuals import load_manuals
from clipo_cs_bot.stakeholders import load_stakeholder_map, tags_for_escalation


def test_load_manuals_combines(tmp_path):
    (tmp_path / "a.md").write_text("문항 출제 방법", encoding="utf-8")
    (tmp_path / "b.txt").write_text("채점 기준", encoding="utf-8")
    (tmp_path / "skip.pdf").write_text("무시", encoding="utf-8")
    out = load_manuals(tmp_path)
    assert "문항 출제 방법" in out
    assert "채점 기준" in out
    assert "a.md" in out
    assert "무시" not in out


def test_load_manuals_empty(tmp_path):
    assert load_manuals(tmp_path) == ""
    assert load_manuals(tmp_path / "missing") == ""


def test_load_manuals_maxchars(tmp_path):
    (tmp_path / "a.md").write_text("가" * 100, encoding="utf-8")
    out = load_manuals(tmp_path, max_chars=20)
    assert len(out) <= 20


def test_stakeholder_map_parsing(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(
        json.dumps({"_comment": "x", "개발팀": "<@U1>", "AI조직": ["<@U2>", "<@U3>"]}),
        encoding="utf-8",
    )
    m = load_stakeholder_map(p)
    assert "_comment" not in m
    assert m["개발팀"] == ["<@U1>"]
    assert m["AI조직"] == ["<@U2>", "<@U3>"]


def test_stakeholder_map_missing(tmp_path):
    assert load_stakeholder_map(tmp_path / "nope.json") == {}


def test_tags_for_escalation():
    m = {"개발팀": ["<@U1>"], "AI조직": ["<@U2>"]}
    assert tags_for_escalation("개발팀: 재현 확인 요청", m) == ["<@U1>"]
    assert tags_for_escalation("정보성 답변 — 즉시 회신 가능", m) == []
    assert tags_for_escalation("개발팀 및 AI조직 확인 필요", m) == ["<@U1>", "<@U2>"]
    assert tags_for_escalation("개발팀", {}) == []


def test_filter_channels_by_keyword():
    chans = [
        {"id": "C1", "name": "clipo-ai-서논술형시스템-cs", "is_member": True},
        {"id": "C2", "name": "clipo-ai-서논술형시스템-dev", "is_member": True},
        {"id": "C3", "name": "clipo-ai-서논술형시스템-notice", "is_member": False},
        {"id": "C4", "name": "random", "is_member": True},
    ]
    out = filter_channels_by_keyword(chans, "clipo-ai-서논술형시스템")
    assert [c[0] for c in out] == ["C1", "C2"]
