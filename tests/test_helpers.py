"""Tests for helper functions in main.py."""

from app.main import snippet, _row_to_dict
from datetime import datetime, timezone


def test_snippet_short():
    assert snippet("Hello world") == "Hello world"


def test_snippet_long():
    long_text = "x" * 100
    result = snippet(long_text, max_len=80)
    assert result.endswith("...")
    assert len(result) == 83  # 80 + "..."


def test_snippet_none():
    assert snippet(None) == ""


def test_snippet_empty():
    assert snippet("") == ""


def test_snippet_whitespace_collapse():
    assert snippet("  hello   world  ") == "hello world"


def test_row_to_dict_basic():
    row = {"id": 1, "subject": "hi"}
    assert _row_to_dict(row) == {"id": 1, "subject": "hi"}


def test_row_to_dict_datetime():
    dt = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)
    row = {"id": 1, "date_sent": dt}
    result = _row_to_dict(row)
    assert result["date_sent"] == dt.isoformat()


def test_row_to_dict_none_values():
    row = {"id": 1, "body": None}
    result = _row_to_dict(row)
    assert result["body"] is None
