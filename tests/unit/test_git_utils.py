from app.services.git_service import parse_commit_range, parse_git_log


def test_parse_commit_range():
    assert parse_commit_range("") == "HEAD~1..HEAD"
    assert parse_commit_range("3") == "HEAD~3..HEAD"
    assert parse_commit_range("HEAD~7..HEAD~2") == "HEAD~7..HEAD~2"


def test_parse_git_log():
    raw = "abc|Ajay|2026-04-23 10:00:00 +0000|first commit\ndef|Nanda|2026-04-24 10:00:00 +0000|second commit"
    rows = parse_git_log(raw)
    assert len(rows) == 2
    assert rows[0]["sha"] == "abc"
    assert rows[1]["message"] == "second commit"
