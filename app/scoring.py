"""Heuristic urgency/priority scoring for Eisenhower Matrix."""

import re
from datetime import datetime, timezone

# Keywords that signal urgency (time-sensitive)
URGENCY_KEYWORDS = [
    r"\burgent\b", r"\basap\b", r"\bdeadline\b", r"\bimmediately\b",
    r"\btime.?sensitive\b", r"\bexpir", r"\btoday\b", r"\btonigh?t\b",
    r"\bby\s+(?:eod|cob|end\s+of\s+day)\b", r"\boverdue\b",
    r"\blast\s+chance\b", r"\bfinal\s+notice\b", r"\breminder\b",
]

# Keywords that signal priority (importance)
PRIORITY_KEYWORDS = [
    r"\bimportant\b", r"\bcritical\b", r"\baction\s+required\b",
    r"\bplease\s+review\b", r"\bapproval\b", r"\bsign\b",
    r"\bcontract\b", r"\binvoice\b", r"\bpayment\b",
    r"\bescalat", r"\bblocking\b", r"\bblocker\b",
    r"\bdecision\b", r"\brequired\b",
]

# Low-priority signals (marketing, newsletters)
LOW_PRIORITY_SIGNALS = [
    r"\bunsubscribe\b", r"\bnewsletter\b", r"\bpromotion\b",
    r"\bno.?reply\b", r"\bmarketing\b", r"\bsale\b",
    r"\b\d+%\s+off\b", r"\bfree\s+shipping\b",
]


def score_email(
    subject: str | None,
    body: str | None,
    from_addr: str | None,
    date_sent: datetime | None,
) -> tuple[float, float]:
    """Return (urgency, priority) scores between 0.0 and 1.0."""
    text = f"{subject or ''} {(body or '')[:1000]}".lower()
    from_lower = (from_addr or "").lower()

    # --- Urgency ---
    urgency = 0.3  # baseline

    # Recency boost: emails from last 24h get urgency bump
    if date_sent:
        now = datetime.now(timezone.utc)
        age_hours = max(0, (now - date_sent).total_seconds() / 3600)
        if age_hours < 4:
            urgency += 0.3
        elif age_hours < 24:
            urgency += 0.2
        elif age_hours < 72:
            urgency += 0.1

    # Keyword boost
    for pattern in URGENCY_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            urgency += 0.1

    # --- Priority ---
    priority = 0.4  # baseline

    # Keyword boost
    for pattern in PRIORITY_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            priority += 0.1

    # Low-priority demotion
    low_signals = sum(1 for p in LOW_PRIORITY_SIGNALS if re.search(p, text, re.IGNORECASE))
    if low_signals >= 2:
        priority -= 0.3
    elif low_signals == 1:
        priority -= 0.15

    # noreply senders are lower priority
    if "noreply" in from_lower or "no-reply" in from_lower:
        priority -= 0.15

    # Clamp to [0, 1]
    urgency = max(0.0, min(1.0, urgency))
    priority = max(0.0, min(1.0, priority))

    return (round(urgency, 3), round(priority, 3))
