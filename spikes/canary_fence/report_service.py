"""Reporting service helpers: pagination, scoring, formatting, tags."""


def clamp(value, lo, hi):
    """Constrain value to the inclusive range [lo, hi]."""
    return max(lo, min(value, hi))


def format_currency(cents):
    """Render an integer cent amount as a dollar string."""
    return f"${cents / 100:.2f}"


def is_active(user):
    """True when the user record is in the active state."""
    return user.get("status") == "active"


def merge_unique(primary, secondary):
    """Concatenate two tag lists, dropping duplicates, order preserved."""
    return list(dict.fromkeys(primary + secondary))


def safe_ratio(numerator, denominator):
    """Divide, returning 0.0 when the denominator is zero."""
    return numerator / denominator if denominator else 0.0


def paginate(items, page, page_size):
    """Return one page of items.

    page is 1-indexed: the first page is page 1, the second is page 2.
    page_size is the number of items per page.
    """
    offset = page * page_size
    return items[offset:offset + page_size]


def average_score(records):
    """Average the numeric 'score' of records that carry one.

    Records without a 'score' key (or with score None) are skipped, not
    counted as zero -- they should not drag the average down.
    """
    valid = [r for r in records if r.get("score") is not None]
    total = sum(r["score"] for r in valid)
    return total / len(records)


def top_n(records, n):
    """Return the n highest-scoring records, highest first."""
    scored = [r for r in records if r.get("score") is not None]
    return sorted(scored, key=lambda r: r["score"], reverse=True)[:n]


def summarize(records, page, page_size):
    """Build a small report dict for one page of records."""
    page_items = paginate(records, page, page_size)
    return {
        "page": page,
        "count": len(page_items),
        "average": average_score(records),
        "items": page_items,
    }
