import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import alerts
import bot


def post(title, amazon_url, key="test-alert"):
    return {
        "title": title,
        "description": amazon_url,
        "content": "",
        "link": "https://www.dealabs.com/bons-plans/" + key,
        "key": key,
        "pubDate": "",
    }


class AlertTests(unittest.TestCase):
    def test_safe_telegram_buttons(self):
        buttons = alerts.deal_buttons(
            "https://amazon.fr/dp/B0B9C4DKKG",
            "https://www.dealabs.com/bons-plans/123",
        )
        self.assertEqual(
            [row[0]["text"] for row in buttons["inline_keyboard"]],
            ["Открыть на Amazon", "Исходное предложение"],
        )
        self.assertIsNone(alerts.deal_buttons(
            "https://amazon.fr.evil.example/dp/B0B9C4DKKG",
            "https://evil.example/123",
        ))
        one = alerts.deal_buttons(
            None, "https://www.lesnumeriques.com/gaming/deal.html"
        )
        self.assertEqual(one["inline_keyboard"][0][0]["text"], "Проверить находку")

    def test_discount_and_price_based_resend(self):
        self.assertTrue(alerts.meaningful_change(None, 0, None, 100))
        self.assertFalse(alerts.meaningful_change(0, 0, None, 80))
        self.assertFalse(alerts.meaningful_change(0, 0, 100, 89))
        self.assertFalse(alerts.meaningful_change(0, 0, 100, 96))
        self.assertTrue(alerts.meaningful_change(0, 0, 100, 79))
        self.assertFalse(alerts.meaningful_change(70, 79, 100, 50))
        self.assertTrue(alerts.meaningful_change(70, 80, 100, 50))
        self.assertTrue(alerts.meaningful_change(0, 50, 100, 100))

    def test_price_watch_renotifies_only_when_stated_price_improves(self):
        first = post("Samsung SSD promo 100 €",
                     "https://www.amazon.fr/dp/B0B9C4DKKG")
        small = post("Samsung SSD promo 90 €",
                     "https://www.amazon.fr/dp/B0B9C4DKKG")
        large = post("Samsung SSD promo 70 €",
                     "https://www.amazon.fr/dp/B0B9C4DKKG")
        with tempfile.TemporaryDirectory() as td, \
             patch.object(bot, "STATE_FILE", Path(td) / "seen.json"), \
             patch.object(bot, "DRY_RUN", True), \
             patch.object(bot, "ENABLE_EARLY_DISCOVERY", True), \
             patch.object(bot, "collect", side_effect=[
                 ([first], 1, 1), ([small], 1, 1), ([large], 1, 1),
                 ([large], 1, 1)
             ]):
            self.assertEqual(bot.run(), 1)
            self.assertEqual(bot.run(), 0)
            self.assertEqual(bot.run(), 1)
            self.assertEqual(bot.run(), 0)

    def test_actual_telegram_payload_has_clickable_buttons(self):
        sent = []
        def fake_urlopen(req, timeout=15):
            sent.append(req.data.decode("utf-8"))
            class Response:
                def __enter__(self):
                    return self
                def __exit__(self, *exc):
                    pass
                def read(self):
                    return b'{"ok": true}'
            return Response()
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "fake-for-test",
            "TELEGRAM_CHAT_ID": "1234",
        }), patch.object(bot, "urlopen", side_effect=fake_urlopen):
            bot.telegram_send(
                "Test",
                alerts.deal_buttons(
                    "https://amazon.fr/dp/B0B9C4DKKG",
                    "https://www.dealabs.com/bons-plans/demo",
                ),
            )
        from urllib.parse import parse_qs
        payload = parse_qs(sent[0])
        reply = json.loads(payload["reply_markup"][0])
        self.assertEqual(len(reply["inline_keyboard"]), 2)

    def test_state_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(bot, "STATE_FILE", Path(td) / "seen.json"):
            bot.save_seen({"abc": 60, "__metadata__": {
                "notified_offer_prices": {"abc": 49},
            }})
            self.assertEqual(bot.get_seen()["abc"], 60)
            self.assertFalse((Path(td) / "seen.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
