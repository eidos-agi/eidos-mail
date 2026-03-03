"""Tests for Eisenhower Matrix (Ike) logic."""

from app.main import _ike_quadrant, _ike_filter_sql, IKE_THRESHOLD


def test_quadrant_do():
    """High urgency + high priority → do first."""
    assert _ike_quadrant(0.8, 0.9) == "do"
    assert _ike_quadrant(0.5, 0.5) == "do"  # exactly at threshold


def test_quadrant_schedule():
    """Low urgency + high priority → schedule."""
    assert _ike_quadrant(0.2, 0.8) == "schedule"
    assert _ike_quadrant(0.1, 0.5) == "schedule"


def test_quadrant_delegate():
    """High urgency + low priority → delegate."""
    assert _ike_quadrant(0.8, 0.2) == "delegate"
    assert _ike_quadrant(0.5, 0.1) == "delegate"


def test_quadrant_eliminate():
    """Low urgency + low priority → eliminate."""
    assert _ike_quadrant(0.1, 0.1) == "eliminate"
    assert _ike_quadrant(0.3, 0.3) == "eliminate"


def test_filter_sql_do():
    sql = _ike_filter_sql("do")
    assert f"urgency >= {IKE_THRESHOLD}" in sql
    assert f"priority >= {IKE_THRESHOLD}" in sql


def test_filter_sql_schedule():
    sql = _ike_filter_sql("schedule")
    assert f"urgency < {IKE_THRESHOLD}" in sql
    assert f"priority >= {IKE_THRESHOLD}" in sql


def test_filter_sql_delegate():
    sql = _ike_filter_sql("delegate")
    assert f"urgency >= {IKE_THRESHOLD}" in sql
    assert f"priority < {IKE_THRESHOLD}" in sql


def test_filter_sql_eliminate():
    sql = _ike_filter_sql("eliminate")
    assert f"urgency < {IKE_THRESHOLD}" in sql
    assert f"priority < {IKE_THRESHOLD}" in sql


def test_filter_sql_unknown():
    """Unknown quadrant returns empty string."""
    assert _ike_filter_sql("bogus") == ""


def test_boundary_values():
    """Test exact boundary at IKE_THRESHOLD."""
    # Exactly at threshold = classified as 'high'
    assert _ike_quadrant(IKE_THRESHOLD, IKE_THRESHOLD) == "do"
    # Just below threshold
    assert _ike_quadrant(IKE_THRESHOLD - 0.01, IKE_THRESHOLD - 0.01) == "eliminate"
