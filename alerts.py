"""Telegram delivery helpers and cautious repeat-alert rules.

These prices are externally advertised RSS headline values, never verified
live Amazon prices.
"""
from urllib.parse import urlsplit


MIN_PRICE_DROP_EUR = 5.0
MIN_PRICE_DROP_FRACTION = 0.20


def meaningful_change(previous_claim, next_claim, previous_price=None, next_price=None):
    """Decide whether the current offer materially improves a sent alert.

    Keep distinct rules for stated-percent and no-percent posts. A missing
    price is never a reason to notify a second time.
    """
    if previous_claim is None:
        return True
    try:
        old_pct = int(previous_claim)
        new_pct = int(next_claim)
    except (ValueError, TypeError):
        return False
    if new_pct >= old_pct + 10:
        return True
    if new_pct > 0 or old_pct > 0:
        return False
    try:
        old_price = float(previous_price)
        new_price = float(next_price)
    except (TypeError, ValueError):
        return False
    if not 5 <= new_price < old_price <= 50000:
        return False
    return (
        old_price - new_price >= MIN_PRICE_DROP_EUR
        and (old_price - new_price) / old_price >= MIN_PRICE_DROP_FRACTION
    )


def deal_buttons(amazon_url=None, source_url=None):
    """Build Telegram inline-keyboard buttons using already-verified HTTPS URLs.

    Do not generate an Amazon product link when none is known.
    """
    buttons = []
    if amazon_url and _allowed_amazon_url(amazon_url):
        buttons.append({"text": "Открыть на Amazon", "url": amazon_url})
    if source_url and _allowed_source_url(source_url):
        buttons.append({
            "text": "Исходное предложение" if amazon_url else "Проверить находку",
            "url": source_url,
        })
    return {"inline_keyboard": [[b] for b in buttons]} if buttons else None


def _allowed_amazon_url(value):
    try:
        p = urlsplit(value)
        return (p.scheme == "https" and p.username is None and p.password is None
                and p.hostname in ("amazon.fr", "amazon.es", "amazon.it")
                and p.path.startswith("/dp/") and p.port in (None, 443))
    except ValueError:
        return False


def _allowed_source_url(value):
    try:
        p = urlsplit(value)
        return (p.scheme == "https" and p.username is None and p.password is None
                and p.port in (None, 443)
                and p.hostname in (
                    "dealabs.com", "www.dealabs.com",
                    "lesnumeriques.com", "www.lesnumeriques.com",
                    "chollometro.com", "www.chollometro.com",
                    "quantospendo.app", "www.quantospendo.app",
                    "brickset.com", "www.brickset.com",
                ))
    except ValueError:
        return False
