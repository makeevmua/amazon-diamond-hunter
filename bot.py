"""Amazon Diamond Hunter FREE — кандидатные скидки, не подтверждённая история цен."""
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.parse import unquote, urlsplit, urlencode
from urllib.request import Request, urlopen

RSS_URL = os.getenv("RSS_URL", "https://www.dealabs.com/rss/hot.xml")
STATE_FILE = Path(os.getenv("STATE_FILE", "seen.json"))
MIN_DISCOUNT = int(os.getenv("MIN_DISCOUNT", "50"))
MAX_NOTIFICATIONS = int(os.getenv("MAX_NOTIFICATIONS", "5"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

CATEGORY_RE = re.compile(
    r"ssd|nvme|disque dur|carte graphique|\bgpu\b|geforce|radeon|rtx\s*\d|"
    r"processeur|\bcpu\b|ryzen|intel|core\s*i[3579]|mémoire|\bram\b|ddr[45]|"
    r"ordinateur|\bpc\b|laptop|monitor|moniteur|écran|ecran|clavier|souris|"
    r"casque|écouteur|microphone|manette|gaming|gamer|playstation|\bps[345]\b|"
    r"xbox|nintendo|switch|jeu vidéo|jeu video|logitech|razer|steelseries|"
    r"corsair|hyperx|samsung|lg|asus|msi|smartphone|tablet|tablette|ipad|"
    r"iphone|pixel|xiaomi|bluetooth|projecteur|oled|qled|téléviseur|"
    r"\btv\b|routeur|wifi|webcam|imprimante\s*3d", re.I)
EXCLUDE_RE = re.compile(
    r"occasion|seconde main|reconditionn|refurbished|used|warehouse|"
    r"abonnement|location|rental|rupture de stock|expiré|expire|fin du deal",
    re.I)
PERCENT_RE = re.compile(
    r"(?:-|−|–|remise\s*de|reduction\s*de|réduction\s*de)\s*(\d{2,3})\s*%", re.I)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?#]|$)", re.I)

def fetch(url, timeout=15):
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; personal RSS reader)",
        "Accept": "application/rss+xml,application/xml,text/xml,text/html,*/*",
    })
    with urlopen(req, timeout=timeout) as response:
        return response.read(2_000_000).decode("utf-8", "replace")

def amazon_link(candidate):
    try:
        candidate = html.unescape(unquote(candidate.replace("\\/", "/"))).rstrip(".,;)\\]")
        parsed = urlsplit(candidate)
        if parsed.scheme not in ("https", "http"):
            return None
        if (parsed.hostname or "").lower() not in ("www.amazon.fr", "amazon.fr", "smile.amazon.fr"):
            return None
        m = ASIN_RE.search(parsed.path)
        if m:
            return "https://www.amazon.fr/dp/" + m.group(1).upper()
    except ValueError:
        pass
    return None

def find_amazon_link(text):
    text = html.unescape(text).replace("\\u002F", "/").replace("\\/", "/")
    for match in URL_RE.findall(text):
        link = amazon_link(match)
        if link:
            return link
    return None

def parse_feed(text):
    root = ET.fromstring(text)
    items = []
    for item in root.findall(".//item"):
        fields = {el.tag.split("}")[-1]: (el.text or "").strip() for el in item}
        items.append({
            "title": fields.get("title", ""),
            "description": fields.get("description", ""),
            "content": fields.get("encoded", ""),
            "link": fields.get("link", ""),
            "key": fields.get("guid") or fields.get("link", ""),
        })
    return items

def percent(title, desc):
    # Discount is only a claim from Dealabs, NEVER a verified historical reduction.
    source = title + " " + re.sub(r"<[^>]*>", " ", desc[:500])
    numbers = [int(x) for x in PERCENT_RE.findall(source)]
    return max((x for x in numbers if 0 <= x <= 95), default=None)

def relevant(title, desc):
    source = title + " " + re.sub(r"<[^>]*>", " ", desc[:450])
    return bool(CATEGORY_RE.search(source) and not EXCLUDE_RE.search(source))

def dealabs_link(link):
    try:
        p = urlsplit(link)
        return p.scheme == "https" and (p.hostname or "").lower() in ("www.dealabs.com", "dealabs.com")
    except ValueError:
        return False

def get_seen():
    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except (ValueError, OSError, TypeError):
        return set()

def save_seen(keys):
    STATE_FILE.write_text(json.dumps(sorted(keys)[-5000:], ensure_ascii=False), encoding="utf-8")

def telegram_send(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID (Actions Secrets)")
    body = urlencode({"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}).encode()
    req = Request("https://api.telegram.org/bot" + token + "/sendMessage", data=body, method="POST")
    try:
        with urlopen(req, timeout=15) as response:
            payload = json.loads(response.read().decode())
            if not payload.get("ok"):
                raise RuntimeError("Telegram rejected message: " + str(payload.get("description", "unknown error")))
    except HTTPError as error:
        # The API response explains 403/400 without printing the bot token or request URL.
        try:
            payload = json.loads(error.read(2048).decode("utf-8", "replace"))
            description = str(payload.get("description", "unspecified"))
        except (ValueError, UnicodeError):
            description = "No readable response from Telegram"
        description = description.replace(token, "[REDACTED]")
        raise RuntimeError(f"Telegram API HTTP {error.code}: {description}") from None

def run():
    if not DRY_RUN and (not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID")):
        raise RuntimeError("Create Telegram bot and set both Actions secrets first.")
    seen = get_seen()
    # Feed can redirect from HTTPS or work only via older HTTP endpoint.
    try:
        items = parse_feed(fetch(RSS_URL))
    except (HTTPError, URLError, TimeoutError):
        if RSS_URL != "https://www.dealabs.com/rss/hot.xml":
            raise
        items = parse_feed(fetch("http://www.dealabs.com/rss/hot.xml"))
    newly_sent = 0
    for item in items:
        title = html.unescape(item["title"])
        desc = item["description"]
        if not relevant(title, desc):
            continue
        discount = percent(title, desc)
        if discount is None or discount < MIN_DISCOUNT:
            continue
        key = item["key"] or item["link"]
        if not key or key in seen:
            continue
        direct = find_amazon_link(item["link"] + " " + desc + " " + item["content"])
        if not direct and dealabs_link(item["link"]):
            try:
                direct = find_amazon_link(fetch(item["link"]))
            except (HTTPError, URLError, TimeoutError, ValueError):
                pass
        if not direct:
            print("SKIPPED no direct Amazon.fr product:", title[:100])
            continue
        message = (
            "💎 КАНДИДАТ — СКИДКА НЕ ПОДТВЕРЖДЕНА\n"
            + title[:180] + "\n\n"
            + f"Заявлено: −{discount}% (история цен НЕ проверена)\n"
            + f"Amazon.fr: {direct}\n"
            + f"Источник: {item['link']}\n\n"
            + "⚠️ Проверь текущую цену, продавца, состояние товара и график Keepa."
        )
        if DRY_RUN:
            print(message + "\n---")
        else:
            telegram_send(message)
            time.sleep(0.4)
        seen.add(key)
        newly_sent += 1
        if newly_sent >= MAX_NOTIFICATIONS:
            break
    save_seen(seen)
    print(f"Checked {len(items)} posts; sent {newly_sent} candidate(s).")
    return newly_sent

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"Diamond Hunter: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
