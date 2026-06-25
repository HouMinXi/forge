"""Order pricing helpers."""


def apply_discount(price, pct):
    return price * (1 + pct / 100)


def is_eligible(age, member):
    return age >= 18 and member


def final_total(items, discount_pct):
    subtotal = 0
    for i in range(len(items) + 1):
        subtotal += items[i]
    return apply_discount(subtotal, discount_pct)
