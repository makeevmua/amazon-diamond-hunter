"""Amazon Diamond Hunter v2 — free RSS discovery; advertised discounts are NOT verified."""
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit, urlencode
from urllib.request import Request, urlopen

FEEDS = [
    "https://www.dealabs.com/rss/hot.xml",
    "https://www.dealabs.com/rss",
    "https://www.dealabs.com/rss/groupe/informatique",
    "https://www.dealabs.com/rss/groupe/consoles-jeux-video",
    "https://www.dealabs.com/rss/groupe/image-son-video",
    "https://www.dealabs.com/rss/groupe/telephonie",
]
RSS_URL = os.getenv("RSS_URL")  # If specified, override all sources for local debugging.
STATE_FILE = Path(os.getenv("STATE_FILE", "seen.json"))
MIN_DISCOUNT = int(os.getenv("MIN_DISCOUNT", "50"))
MAX_NOTIFICATIONS = min(8, int(os.getenv("MAX_NOTIFICATIONS", "5")))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
REPORT_STATUS = os.getenv("REPORT_STATUS", "false").lower() == "true"

CATEGORY_RE = re.compile(
    r"\b(ssd|nvme|gpu|geforce|radeon|rtx|cpu|ryzen|ddr[45]|ram|laptop|"
    r"ordinateur|pc|monitor|oled|qled|wifi|bluetooth|gaming|gamer|xbox|"
    r"nintendo|switch|playstation|ps[345]|logitech|razer|steelseries|"
    r"corsair|hyperx|samsung|asus|msi|ipad|iphone|pixel|xiaomi|"
    r"smartphone|webcam|chromebook)\b|carte graphique|disque dur|"
    r"processeur|mémoire|ecran|écran|moniteur|clavier|souris|"
    r"casque|écouteurs?|microphone|manettes?|jeu[x]? vidéo|"
    r"tablette|imprimante 3d|téléviseur|routeur|projecteur|"
    r"carte mère|alimentation pc|console de jeu", re.I,
)
EXCLUDE_RE = re.compile(
    r"occasion|seconde main|reconditionn|refurbished|\bused\b|"
    r"warehouse|abonnement|location|rental|rupture de stock|"
    r"expiré|fin du deal|écoulé|épuisé", re.I,
)
PERCENT_RE = re.compile(
    r"(?:(?:-|−|–)\s*|remise\s+de\s*|"
    r"r[eé]duction\s+de\s*|économie\s+de\s*|discount\s+de\s*)"
    r"(\d{2,3})\s*%", re.I,
)
PAIR_RE = re.compile(
    r"(?P<now>\d{1,5}(?:[.,]\d{1,2})?)\s*€\s*"
    r"(?:au\s+lieu\s+de|contre|plut[oô]t\s+que)\s*"
    r"(?P<before>\d{1,5}(?:[.,]\d{1,2})?)\s*€", re.I,
)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?#]|$)", re.I)
BRAND_RE = re.compile(
    r"samsung|sony|lg|asus|msi|logitech|corsair|razer|"
    r"steelseries|hyperx|western digital|wd black|crucial|"
    r"nintendo|playstation|xbox|amd|intel|nvidia|"
    r"apple|kingston|seagate|lenovo|dell|hp|bose|"
    r"jbl|anker|thrustmaster|elgato", re.I
)
ERROR_RE = re.compile(r"erreur\s+de\s+prix|bug\s+de\s+prix|price\s+error", re.I)


def fetch(url, timeout=15):
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; RSS Reader 1.0)",
        "Accept": "application/rss+xml,application/xml,text/xml,text/html,*/*",
    })
    with urlopen(request, timeout=timeout) as response:
        return response.read(2_000_000).decode("utf-8", "replace")


def amazon_link(candidate):
    try:
        candidate = html.unescape(unquote(candidate.replace("\\/", "/"))).rstrip(".,;)\\]")
        parsed = urlsplit(candidate)
        if parsed.scheme not in ("http", "https"):
            return None
        if (parsed.hostname or "").lower() not in (
            "www.amazon.fr", "amazon.fr", "smile.amazon.fr"
        ):
            return None
        match = ASIN_RE.search(parsed.path)
        return "https://www.amazon.fr/dp/" + match.group(1).upper() if match else None
    except ValueError:
        return None


def find_amazon_link(text):
    cleaned = html.unescape(text).replace("\\u002F", "/").replace("\\/", "/")
    for url in URL_RE.findall(cleaned):
        direct = amazon_link(url)
        if direct:
            return direct
    return None


def parse_feed(text):
    root = ET.fromstring(text)
    items = []
    for entry in root.findall(".//item"):
        fields = {
            child.tag.split("}")[-1]: (child.text or "").strip()
            for child in entry
        }
        items.append({
            "title": fields.get("title", ""),
            "description": fields.get("description", ""),
            "content": fields.get("encoded", ""),
            "link": fields.get("link", ""),
            "key": fields.get("guid") or fields.get("link", ""),
            "pubDate": fields.get("pubDate", ""),
        })
    return items


def percent(title, desc):
    source = html.unescape(title + " " + re.sub(r"<[^>]*>", " ", desc[:1000]))
    claims = []
    for match in PERCENT_RE.finditer(source):
        value = int(match.group(1))
        prefix = source[max(0, match.start() - 19):match.start()].lower()
        if 50 <= value <= 95 and not re.search(r"jusqu['’]?\s*[àa]\s*$", prefix):
            claims.append(value)
    return max(claims) if claims else None


def price_pair(text):
    plain = html.unescape(re.sub(r"<[^>]*>", " ", text[:1200]))
    for match in PAIR_RE.finditer(plain):
        current = float(match.group("now").replace(",", "."))
        previous = float(match.group("before").replace(",", "."))
        if 5 <= current < previous <= 50000:
            claimed = round(100 * (1 - current / previous))
            if 50 <= claimed <= 95:
                return claimed, current, previous
    return None


def relevant(title, desc):
    source = html.unescape(title + " " + re.sub(r"<[^>]*>", " ", desc[:600]))
    return bool(CATEGORY_RE.search(source) and not EXCLUDE_RE.search(source))


def dealabs_link(link):
    try:
        p = urlsplit(link)
        return p.scheme == "https" and (p.hostname or "").lower() in (
            "www.dealabs.com", "dealabs.com"
        )
    except ValueError:
        return False


def fresh_enough(pubdate):
    if not pubdate:
        return True
    try:
        dt = parsedate_to_datetime(pubdate)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= datetime.now(timezone.utc) - timedelta(hours=48)
    except (ValueError, TypeError):
        return True


def get_seen():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {k: 100 for k in data if isinstance(k, str)}
    except (ValueError, OSError, TypeError):
        pass
    return {}


def save_seen(seen):
    # Values store the highest notified *claimed* discount for each deal or ASIN.
    # No Amazon price history is inferred from these values.
    subset = dict(list(seen.items())[-5000:])
    STATE_FILE.write_text(json.dumps(subset, ensure_ascii=False), encoding="utf-8")


def telegram_send(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID (Actions Secrets)")
    body = urlencode({
        "chat_id": chat_id, "text": message,
        "disable_web_page_preview": "true"
    }).encode()
    request = Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=body, method="POST"
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode())
            if not result.get("ok"):
                raise RuntimeError("Telegram rejected message")
    except HTTPError as error:
        try:
            data = json.loads(error.read(2048).decode("utf-8", "replace"))
            reason = str(data.get("description", "unknown error"))
        except ValueError:
            reason = "unknown error"
        raise RuntimeError(
            f"Telegram API HTTP {error.code}: " + reason.replace(token, "[REDACTED]")
        ) from None


def collect():
    feed_urls = [RSS_URL] if RSS_URL else FEEDS
    posts = {}
    ok_feeds = 0
    for url in feed_urls:
        try:
            xml_text = fetch(url)
            items = parse_feed(xml_text)
            ok_feeds += 1
            print(f"FEED OK: {url}: {len(items)} posts")
            for item in items:
                unique = item["key"] or item["link"]
                if unique and unique not in posts:
                    posts[unique] = item
        except (HTTPError, URLError, TimeoutError, ET.ParseError, ValueError) as error:
            print(f"FEED unavailable: {url}: {type(error).__name__}")
    if not ok_feeds:
        raise RuntimeError("All RSS sources failed; check Dealabs feed accessibility.")
    return list(posts.values()), ok_feeds, len(feed_urls)


def candidates(posts):
    result = []
    for item in posts:
        title = html.unescape(item["title"])
        desc = item["description"]
        if not fresh_enough(item["pubDate"]) or not relevant(title, desc):
            continue
        claimed_percent = percent(title, desc)
        pair = price_pair(title + " " + desc)
        reduction = max(claimed_percent or 0, pair[0] if pair else 0)
        if reduction < MIN_DISCOUNT:
            continue
        # Only trusted Dealabs source links, and Amazon.fr direct product ASIN.
        if not dealabs_link(item["link"]):
            continue
        direct = find_amazon_link(
            item["link"] + " " + desc + " " + item["content"]
        )
        # A deal page may expose the merchant's direct URL when RSS does not.
        if not direct:
            try:
                direct = find_amazon_link(fetch(item["link"]))
            except (HTTPError, URLError, TimeoutError, ValueError):
                pass
        if not direct:
            continue
        priority = reduction + 8 * bool(ERROR_RE.search(title)) + 5 * bool(BRAND_RE.search(title))
        result.append({
            "item": item, "title": title, "amazon": direct,
            "discount": reduction, "pair": pair,
            "priority": priority,
        })
    return sorted(result, key=lambda d: (d["priority"], d["discount"]), reverse=True)


def run():
    if not DRY_RUN and (
        not os.getenv("TELEGRAM_BOT_TOKEN") or
        not os.getenv("TELEGRAM_CHAT_ID")
    ):
        raise RuntimeError("Create Telegram bot and set both Actions secrets first.")
    posts, available, requested = collect()
    seen = get_seen()
    found = candidates(posts)
    sent = 0
    for deal in found:
        item = deal["item"]
        asin_key = deal["amazon"]
        # Notify again only if *claimed* discount grows at least 10 percentage points.
        if int(seen.get(asin_key, 0)) + (10 if asin_key in seen else 0) > deal["discount"]:
            continue
        if item["key"] in seen and int(seen[item["key"]]) >= deal["discount"]:
            continue
        pair = deal["pair"]
        prices = (
            f"\nВ объявлении: {pair[1]:.2f} € вместо {pair[2]:.2f} €"
            if pair else ""
        )
        level = "⚡ ВОЗМОЖНАЯ ЦЕНОВАЯ ОШИБКА" if ERROR_RE.search(deal["title"]) else "💎 КАНДИДАТ НА СКИДКУ"
        message = (
            f"{level} · НЕ ПОДТВЕРЖДЕНО\n"
            + deal["title"][:170] + "\n\n"
            + f"Заявленная скидка: −{deal['discount']}%" + prices + "\n"
            + "История цен Amazon: НЕ проверена\n"
            + f"Amazon.fr: {deal['amazon']}\n"
            + f"Первоисточник: {item['link']}\n\n"
            + "Проверь цену, продавца, состояние товара и график Keepa перед покупкой."
        )
        if DRY_RUN:
            print(message + "\n---")
        else:
            telegram_send(message)
            time.sleep(0.4)
        seen[asin_key] = deal["discount"]
        seen[item["key"]] = deal["discount"]
        sent += 1
        if sent >= MAX_NOTIFICATIONS:
            break
    save_seen(seen)
    summary = (
        f"💎 Diamond Hunter: проверено {len(posts)} публикаций из "
        f"{available}/{requested} лент. Кандидатов 50%+: {len(found)}, "
        f"новых уведомлений: {sent}."
    )
    print(summary)
    if REPORT_STATUS and not DRY_RUN:
        telegram_send(summary + "\nБез подтверждённой истории цен предложения не считаются доказанными скидками.")
    return sent


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print("Diamond Hunter:", type(error).__name__, str(error), file=sys.stderr)
        sys.exit(1)
