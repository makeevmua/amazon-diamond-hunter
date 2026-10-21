"""Strict product-scope rules shared by discovery, alerts and the storefront.

Match the *product being offered*, not page sidebar or unrelated brand mentions.
Only Xbox is removed from gaming; PlayStation, Nintendo and PC stay eligible.
Samsung is eligible only when the headline clearly describes an SSD.
"""
import re

XBOX = re.compile(
    r"(?i)\b(?:x[\s-]?box|xbox\s*game\s*pass|xbox\s*live|"
    r"series\s*[sx]\s*\(?xbox\)?|xb[\s-]?one)\b"
)
SAMSUNG = re.compile(r"(?i)\bsamsung\b")
GALAXY_PRODUCT = re.compile(
    r"(?i)\b(?:galaxy\s*(?:s\d{1,2}|a\d{1,2}|z\s*(?:flip|fold)|"
    r"tab|buds|watch|book|fit|ring|note\d*)|"
    r"coque\s+(?:pour\s+)?galaxy|case\s+(?:for\s+)?galaxy)\b"
)
SAMSUNG_SSD = re.compile(
    r"(?i)\b(?:ssd|nvme|m\.?2|solid[\s-]?state\s+drive|"
    r"disque\s*(?:dur\s*)?ssd|"
    r"(?:9100|990|980|970|870|860)\s*(?:pro|evo|qvo)|"
    r"t[579]\s*(?:shield|portable|ssd)|p3\s*plus)\b"
)
NON_SSD_SAMSUNG = re.compile(
    r"(?i)\b(?:galaxy|smartphone|t[ée]l[ée]phone|phone|tablet|tablette|"
    r"smartwatch|tv|t[ée]l[ée]viseur|monitor|moniteur|odyssey|"
    r"[ée]couteurs?|buds|earbuds|coque|housse|case|film\s+de\s+protection|"
    r"chargeur\s+(?:pour\s+)?(?:galaxy|samsung)|"
    r"powerbank|batterie\s+externe|micro\s*sd|carte\s+m[ée]moire|"
    r"lave[\s-]?linge|r[ée]frig[ée]rateur|aspirateur)\b"
)


def is_allowed(title, description=""):
    """Conservatively classify the offer headline, not a whole merchant page.

    Use description only to catch explicit compatibility for ambiguous generic
    titles; avoid rejecting a PS5 deal just because comparisons mention Xbox.
    """
    title = re.sub(r"<[^>]*>", " ", str(title or ""))
    description = re.sub(r"<[^>]*>", " ", str(description or ""))
    if XBOX.search(title):
        return False
    if GALAXY_PRODUCT.search(title):
        return False
    if SAMSUNG.search(title):
        return bool(SAMSUNG_SSD.search(title) and not NON_SSD_SAMSUNG.search(title))
    # Generic accessory listing with explicit Xbox-only compatibility: filter.
    if XBOX.search(description) and re.search(
        r"(?i)\b(?:manette|controller|contr[oô]leur|headset|"
        r"accessoire|accessory|station\s+de\s+charge|"
        r"support\s+pour|cable\s+pour)\b", title
    ) and not re.search(r"(?i)\b(?:ps[345]|playstation|pc|ordinateur|switch)\b", title):
        return False
    return True
