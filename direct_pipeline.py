"""Combine first-party Amazon quotes with existing candidacy and catalog logic.

Only direct API featured new offers qualify. External "saving basis" is a
reference price, not proof of a historical low. Previous observed changes in
a strictly short-lived cache can be used as a stronger independent signal.
"""
from urllib.parse import urlsplit
import policy


def keepa_candidate(item, category_fn, interior_threshold=25, regular_threshold=25):
    """Independently observed Keepa price falls; no Amazon checkout claim."""
    if item.get("direct_source") != "Keepa Deals":
        return None
    title = item.get("title", "")
    if not policy.is_allowed(title):
        return None
    categories = category_fn(title)
    if not categories:
        categories = ["other"]
    threshold = 25
    drop = item.get("keepa_drop90_percent")
    if type(drop) not in (int, float) or drop < threshold:
        return None
    price = item.get("direct_current_eur")
    if type(price) not in (int, float) or price <= 0:
        return None
    link = item.get("link")
    market = (urlsplit(link or "").hostname or "").lower()
    if market not in ("amazon.fr", "amazon.de", "amazon.es", "amazon.it"):
        return None
    return {
        "item": item, "title": title, "amazon": link,
        "discount": int(drop), "pair": None,
        "priority": 50 + int(drop),
        "market": market, "category": ", ".join(categories),
        "source_only": False, "merchant_comparison": False,
        "signal": "keepa_recent_drop", "headline_price": price,
        "public_baseline": None, "image": item.get("image"),
        "direct_offer": True,
        "direct_observed_pct": None, "direct_list_pct": 0,
        "direct_reference": item.get("keepa_reference90_eur"),
        "direct_merchant": item.get("direct_merchant") or "не определён",
        "direct_affiliate_url": link,
        "source_provider": "Keepa Deals",
        "keepa_price_type": item.get("keepa_price_type"),
        "keepa_changed_at": item.get("keepa_changed_at"),
    }


def to_candidate(item, category_fn, interior_threshold=25, regular_threshold=25):
    if item.get("direct_source") not in ("Amazon Creators API", "Bright Data Amazon Search"):
        return None
    title = item.get("title", "")
    if not policy.is_allowed(title):
        return None
    categories = category_fn(title)
    if not categories:
        categories = ["other"]
    threshold = 25
    listed_pct = int(item.get("direct_claimed_percent") or 0)
    observed_pct = int(item.get("direct_observed_drop") or 0)
    # Actual previous observed price is stronger than Amazon's list/reference
    # price. Allow observed 30% drop if the absolute product is relevant.
    good_direct = listed_pct >= threshold or observed_pct >= threshold
    if not good_direct:
        return None
    current = item.get("direct_current_eur")
    if not isinstance(current, (float, int)) or current <= 0:
        return None
    link = item["link"]
    market = (urlsplit(link).hostname or "").lower()
    if market not in ("amazon.fr", "amazon.de", "amazon.es", "amazon.it"):
        return None
    claimed_saving = max(0, listed_pct)
    alert_pct = max(claimed_saving, observed_pct)
    return {
        "item": item, "title": title, "amazon": link,
        "discount": alert_pct, "pair": None, "priority": alert_pct + 30,
        "market": market, "category": ", ".join(categories),
        "source_only": False, "merchant_comparison": False,
        "signal": "direct_observed_drop" if observed_pct >= threshold
                  else "direct_amazon_promo",
        "headline_price": current, "public_baseline": None,
        "image": item.get("image"),
        "direct_offer": True,
        "direct_observed_pct": observed_pct,
        "direct_list_pct": listed_pct,
        "direct_reference": item.get("direct_list_price_eur"),
        "direct_merchant": item.get("direct_merchant") or "не указан",
        "direct_affiliate_url": item.get("direct_affiliate_url") or link,
        "source_provider": item.get("direct_source"),
        "search_snapshot_at": item.get("search_snapshot_at"),
    }


def merge_candidates(community, direct):
    """Native Amazon candidates first; exact ASIN duplicates not double sent."""
    seen = set()
    combined = []
    for candidate in [*direct, *community]:
        exact = candidate.get("amazon") or candidate["item"]["link"]
        if exact in seen:
            continue
        seen.add(exact)
        combined.append(candidate)
    return combined


def format_direct_alert(deal):
    """Never claim seller, France delivery or checkout price verified."""
    title = deal["title"][:170]
    current = deal["headline_price"]
    market = deal["market"]
    observed = deal.get("direct_observed_pct") or 0
    listed = deal.get("direct_list_pct") or 0
    if deal.get("source_provider") == "Bright Data Amazon Search":
        return (f"🔍 AMAZON SEARCH: ПРЕДПОЛАГАЕМАЯ АКЦИЯ\n{title}\n\n"
                f"Цена из поисковой выдачи Amazon по Bright Data: {current:.2f} €\n"
                f"Разница с исходной/перечёркнутой ценой: {listed}% "
                "(НЕ историческая скидка).\n"
                f"Дата снимка источника: {deal.get('search_snapshot_at') or 'неизвестна'}\n"
                f"Страна: {market}\n"
                "Цена в корзине, наличие и доставка во Францию не подтверждены.\n"
                f"Открыть: {deal['amazon']}")
    if deal["signal"] == "keepa_recent_drop":
        reference = deal.get("direct_reference")
        changed = deal.get("keepa_changed_at") or "не указано"
        kind = ("Amazon" if deal.get("keepa_price_type") == "amazon"
                else "новый товар у продавцов маркетплейса")
        return (f"⚡ KEEPA: СВЕЖЕЕ ПАДЕНИЕ ЦЕНЫ\n{title}\n\n"
                f"Цена по Keepa ({kind}): {current:.2f} €\n"
                f"Ниже средней за 90 дней на {deal['discount']}% "
                f"(средняя {reference:.2f} €)\n"
                f"Изменение зафиксировано: {changed}\n"
                f"Регион: {market}\n"
                "Не подтверждено в корзине Amazon, доставка во Францию не проверена.\n"
                f"Открыть: {deal['amazon']}\n"
                "⚠️ Данные Keepa могут поступать с задержкой.")
    if deal["signal"] == "direct_observed_drop":
        intro = "⚡ AMAZON: ЦЕНА УПАЛА ПРИ ПРЯМОЙ ПРОВЕРКЕ"
        savings = f"Падение относительно недавних замеров API: {observed}% (не история за 90 дней)"
    else:
        intro = "💎 AMAZON: НОВОЕ ПРЕДЛОЖЕНИЕ"
        savings = f"Разница с референсной ценой Amazon: {listed}% (не подтверждённая рыночная скидка)"
    return (f"{intro}\n{title}\n\n"
            f"Цена в Amazon API: {current:.2f} €\n"
            f"{savings}\n"
            f"Магазин/продавец по API: {deal.get('direct_merchant') or 'неизвестен'}\n"
            f"Регион: {market}\n"
            f"Доставка во Францию и итог корзины НЕ проверены.\n"
            f"Amazon: {deal['direct_affiliate_url']}\n"
            f"⚠️ Данные могут измениться в любой момент.")
