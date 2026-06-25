"""Account ledger helpers: pagination, affordability, formatting, totals."""


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


def running_total(amounts):
    """Return the cumulative sum after each amount, in order."""
    total = 0
    out = []
    for amount in amounts:
        total += amount
        out.append(total)
    return out


def percent_change(old, new):
    """Percent change from old to new; 0.0 when old is zero."""
    return (new - old) / old * 100 if old else 0.0


def paginate(items, page, page_size):
    """Return one page of items.

    page is 1-indexed: the first page is page 1, the second is page 2.
    """
    offset = page * page_size
    return items[offset:offset + page_size]


def can_afford(cost, balance):
    """True when the balance covers the cost.

    A cost exactly equal to the balance still affords (spends to zero).
    """
    return cost < balance


def select_top(records, n):
    """Return the n highest-scoring records, highest score first."""
    scored = [r for r in records if r.get("score") is not None]
    return sorted(scored, key=lambda r: r["score"], reverse=True)[:n]
