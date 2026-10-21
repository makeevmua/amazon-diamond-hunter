"""Fast direct-only Amazon watcher for an always-on PC/VPS.

Run separately from GitHub Actions. This process has zero forum scraping and
uses only approved Amazon Creators API credentials. It does not run until
secrets are configured, nor does it attempt credential bypass.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import amazon_direct
import direct_pipeline
import alerts
import media
import bot


def _read_state(path):
    try:
        doc = json.loads(path.read_text("utf8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf8")
    os.replace(temp, path)


def scan(state, deliver):
    """Return notification count; quiet if no fresh qualified Amazon offers."""
    now = time.time()
    docs = amazon_direct.discover(state)
    amazon_direct.add_watchlist(state, [doc["link"] for doc in docs])
    recent = state.setdefault("worker_alert_state", {})
    sent = 0
    try:
        max_alerts = max(1, min(int(os.getenv("DIRECT_ALERTS_PER_CYCLE", "4")), 8))
    except ValueError:
        max_alerts = 4
    for item in docs:
        candidate = direct_pipeline.to_candidate(item, bot.category_matches,
                                         bot.INTERIOR_DISCOUNT, bot.MIN_DISCOUNT)
        if not candidate:
            continue
        key = candidate["amazon"]
        previous = recent.get(key)
        previous_pct = previous.get("discount") if isinstance(previous, dict) else None
        previous_price = previous.get("price") if isinstance(previous, dict) else None
        current_price = candidate["headline_price"]
        if previous_pct is not None:
            # Do not flood the chat with a "new" offer at identical value.
            pct_changed = candidate["discount"] >= previous_pct + 10
            price_changed = (previous_price is not None and
                             current_price <= previous_price * .8 and
                             previous_price - current_price >= 5)
            if not (pct_changed or price_changed):
                continue
        message = direct_pipeline.format_direct_alert(candidate)
        buttons = alerts.deal_buttons(candidate["amazon"])
        if buttons:
            buttons["inline_keyboard"][0][0]["url"] = candidate["direct_affiliate_url"]
        deliver(message, buttons, media.safe_image_url(candidate["image"]))
        recent[key] = {"discount": candidate["discount"], "price": current_price,
                       "at": now}
        sent += 1
        if sent >= max_alerts:
            break
    state["worker_alert_state"] = {k: v for k, v in recent.items() if
                                   isinstance(v, dict) and
                                   now - v.get("at", 0) < 23 * 3600}
    return sent


def main():
    if not amazon_direct.enabled():
        print("FAST HUNTER: no approved Amazon Creators API credentials; nothing was started.",
              file=sys.stderr)
        return 2
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        print("FAST HUNTER: Telegram credentials missing.", file=sys.stderr)
        return 2
    statefile = Path(os.getenv("FAST_HUNTER_STATE_PATH", "fast-hunter-state.json"))
    try:
        seconds = max(60, min(3600, int(os.getenv("DIRECT_INTERVAL_SECONDS", "90"))))
    except ValueError:
        seconds = 90
    state = _read_state(statefile)
    seeded = [x.strip() for x in os.getenv("DIRECT_WATCH_ASINS", "").split(",") if x.strip()]
    amazon_direct.add_watchlist(state, seeded)
    print(f"FAST HUNTER: direct Amazon worker running every {seconds}s; "
          "not a guarantee of catching every flash price error.", flush=True)
    def deliver(message, buttons, image):
        bot.telegram_send(message, buttons=buttons, photo_url=image)
    while True:
        started = time.monotonic()
        try:
            n = scan(state, deliver)
            print(f"FAST HUNTER: {datetime.now(timezone.utc).isoformat()} sent={n}", flush=True)
        except Exception as error:
            print(f"FAST HUNTER: scan failed {type(error).__name__}", flush=True)
        finally:
            _write_state(statefile, state)
        time.sleep(max(1, seconds - (time.monotonic() - started)))


if __name__ == "__main__":
    sys.exit(main())
