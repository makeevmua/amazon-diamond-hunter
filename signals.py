"""Conservative discovery signals from PUBLIC DEAL POSTS, not current Amazon prices.

Never turn a claimed discount or previous RSS price into verified savings.
"""
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from statistics import median

ERROR_RE = re.compile(
    r"\b(?:erreur\s+de\s+prix|bug\s+de\s+prix|prix\s+erron[eé]|"
    r"error\s+de\s+precio|fallo\s+de\s+precio|"
    r"errore\s+di\s+prezzo|"
    r"price\s+(?:glitch|error|mistake)|pricing\s+error)\b", re.I,
)
PROMO_RE = re.compile(
    r"\b(?:bon\s+plan|offre|promo(?:tion)?|soldes?|"
    r"deal|descuento|oferta|offerta|sconto|"
    r"remise|reduction|réduction|discount|"
    r"baisse|bajada|risparmio|deal\s+flash|"
    r"lightning\s+deal|coupon)\b", re.I,
)
# The offer price MUST appear in the HEADLINE. Some RSS descriptions include
# other products, recommended prices or unrelated shipping fees.
EURO_PRICE_RE = re.compile(
    r"(?<![\w.,])("
    r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[,.]\d{1,2})?|"
    r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|"
    r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|"
    r"\d{1,5}(?:[.,]\d{1,2})?"
    r")\s*(?:€|euros?\b|EUR\b)", re.I
)
CREDIT_CONTEXT_RE = re.compile(
    r"(?:sur\s+(?:la\s+)?carte\s+(?:de\s+)?fid[eé]lit[eé]|"
    r"en\s+(?:bon|carte)\s+d['’]achat|"
    r"store\s+credit|gift\s+card|cashback|"
    r"carte\s+cadeau|saldo\s+regalo|vale\s+de\s+compra)",
    re.I,
)
MIN_OBSERVATION_AGE_SECONDS = 12 * 3600
MIN_BASELINE_SPAN_SECONDS = 24 * 3600
MIN_DROP_PCT = 40.0


def headline_price(title):
    """One unambiguous EUR price in the headline; never infer from identifiers."""
    matches = []
    for match in EURO_PRICE_RE.finditer(title):
        raw = match.group(1).replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
        # "1 599,00", "1.599,00", "1,599.00" and "1599.00"
        # use different locale conventions; normalize only when unambiguous.
        if "." in raw and "," in raw:
            raw = (raw.replace(".", "").replace(",", ".")
                   if raw.rfind(",") > raw.rfind(".") else
                   raw.replace(",", ""))
        elif raw.count(".") == 1 and len(raw.split(".")[-1]) == 3:
            raw = raw.replace(".", "")
        elif raw.count(",") == 1 and len(raw.split(",")[-1]) == 3:
            raw = raw.replace(",", "")
        elif raw.count(".") > 1:
            raw = raw.replace(".", "")
        elif raw.count(",") > 1:
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(",", ".")
        try:
            price = float(raw)
        except ValueError:
            continue
        suffix = title[match.end():match.end() + 55]
        if CREDIT_CONTEXT_RE.search(suffix):
            continue
        if 5 <= price <= 50000:
            matches.append(price)
    # Multi-price headlines may represent different variants, credit and
    # before/after numbers. Never pick one by guesswork.
    return round(matches[0], 2) if len(matches) == 1 else None


def publication_seconds(pubdate, default=None):
    if pubdate:
        try:
            d = parsedate_to_datetime(pubdate)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return int(d.timestamp())
        except (TypeError, ValueError, OverflowError):
            pass
    return int(default if default is not None else datetime.now(timezone.utc).timestamp())


def reference_drop(observations, current_price, post_key, published_at):
    """Compare against INDEPENDENT historical third-party offer publications.

    At least two distinct previous publications over 24h, none within the
    last 12h. No extrapolation of Amazon price history.
    """
    previous = []
    seen_posts = set()
    for item in observations if isinstance(observations, list) else []:
        if not isinstance(item, dict):
            continue
        key = item.get("post")
        value = item.get("price")
        timestamp = item.get("at")
        if not key or key == post_key or key in seen_posts:
            continue
        if not isinstance(value, (int, float)) or not 5 <= value <= 50000:
            continue
        if not isinstance(timestamp, (int, float)):
            continue
        if timestamp > published_at - MIN_OBSERVATION_AGE_SECONDS:
            continue
        previous.append((int(timestamp), float(value)))
        seen_posts.add(key)
    if len(previous) < 2:
        return None
    if max(t for t, _ in previous) - min(t for t, _ in previous) < MIN_BASELINE_SPAN_SECONDS:
        return None
    reference = median(v for _, v in previous)
    if reference <= 0:
        return None
    fall = round(100 * (reference - current_price) / reference, 1)
    if fall < MIN_DROP_PCT:
        return None
    return {"reference": round(reference, 2), "decrease_percent": fall,
            "previous_posts": len(previous)}


def record_observation(history, market_asin, post_key, price, first_seen):
    """Record one public *offer listing* price per RSS post, not per scan.

    Does not poll Amazon and does not claim live price verification.
    """
    if not market_asin or not post_key or price is None:
        return
    rows = history.setdefault(market_asin, [])
    if not isinstance(rows, list):
        history[market_asin] = rows = []
    if not any(isinstance(x, dict) and x.get("post") == post_key for x in rows):
        rows.append({"post": post_key[:200], "price": round(float(price), 2),
                     "at": int(first_seen)})
    history[market_asin] = rows[-20:]


def prune_history(history):
    """Bound data volume stored through Actions cache."""
    keys = list(history)
    for key in keys[:-250]:
        history.pop(key, None)
    return history
