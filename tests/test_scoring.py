"""Tests for urgency/priority scoring heuristics."""

from datetime import datetime, timezone, timedelta
from app.scoring import score_email


def test_baseline_scores():
    """No signals → baseline scores."""
    u, p = score_email("Hello", "Just a message", "friend@example.com", None)
    assert 0.0 <= u <= 1.0
    assert 0.0 <= p <= 1.0


def test_urgent_keywords_boost_urgency():
    """Urgency keywords should raise urgency score."""
    u_plain, _ = score_email("Hello", "Normal message", "a@b.com", None)
    u_urgent, _ = score_email("URGENT: Action needed", "This is urgent asap", "a@b.com", None)
    assert u_urgent > u_plain


def test_priority_keywords_boost_priority():
    """Priority keywords should raise priority score."""
    _, p_plain = score_email("Hello", "Normal message", "a@b.com", None)
    _, p_important = score_email("Important: Contract review", "Action required approval needed", "a@b.com", None)
    assert p_important > p_plain


def test_recent_email_more_urgent():
    """Very recent email should be more urgent than old one."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)
    u_recent, _ = score_email("Hi", "body", "a@b.com", now)
    u_old, _ = score_email("Hi", "body", "a@b.com", old)
    assert u_recent > u_old


def test_noreply_lower_priority():
    """noreply sender should lower priority."""
    _, p_human = score_email("Hello", "body", "alice@example.com", None)
    _, p_noreply = score_email("Hello", "body", "noreply@example.com", None)
    assert p_noreply < p_human


def test_marketing_signals_lower_priority():
    """Marketing/newsletter signals should lower priority."""
    _, p_normal = score_email("Hello", "Let's chat", "a@b.com", None)
    _, p_spam = score_email("50% OFF SALE!", "unsubscribe newsletter promotion", "noreply@store.com", None)
    assert p_spam < p_normal


def test_scores_clamped():
    """Scores should stay within [0, 1] even with extreme signals."""
    # Max urgency signals
    now = datetime.now(timezone.utc)
    u, p = score_email(
        "URGENT DEADLINE TODAY ASAP",
        "urgent immediately overdue last chance final notice reminder time-sensitive expiring",
        "a@b.com",
        now,
    )
    assert u <= 1.0
    assert p <= 1.0

    # Max low-priority signals
    u2, p2 = score_email(
        "Newsletter: 50% off sale",
        "unsubscribe promotion marketing free shipping",
        "noreply@spam.com",
        now - timedelta(days=365),
    )
    assert u2 >= 0.0
    assert p2 >= 0.0


def test_none_inputs():
    """Should handle None inputs gracefully."""
    u, p = score_email(None, None, None, None)
    assert 0.0 <= u <= 1.0
    assert 0.0 <= p <= 1.0
