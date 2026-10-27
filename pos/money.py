# money math, paise only, floats are how you end up 1 paisa off on an invoice
# half-up matches validate_seed.py so our totals tie out with their sample invoice

from decimal import Decimal, ROUND_HALF_UP


def to_paise(value):
    # json money string in, paise out, half up. no float() anywhere
    amount = Decimal(str(value))
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money_string(paise):
    sign = "-" if paise < 0 else ""
    paise = abs(int(paise))
    return "{}{}.{:02d}".format(sign, paise // 100, paise % 100)


def round_ratio_half_up(numerator, denominator):
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    return (2 * numerator + denominator) // (2 * denominator)


def percentage(paise, bps):
    return round_ratio_half_up(int(paise) * int(bps), 10000)


def inclusive_tax(gross_after_discount_paise, rate_bps):
    # price already has gst inside it, pull it back out per line
    gross = int(gross_after_discount_paise)
    taxable = round_ratio_half_up(gross * 10000, 10000 + int(rate_bps))
    tax = gross - taxable
    cgst = round_ratio_half_up(tax, 2)
    sgst = tax - cgst
    assert taxable + cgst + sgst == gross
    return {"taxable_paise": taxable, "tax_paise": tax,
            "cgst_paise": cgst, "sgst_paise": sgst}
