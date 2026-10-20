import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
import signals


def post(title, desc, key="deal-x", pubdate=""):
    return {
        "title": title, "description": desc, "key": key, "pubDate": pubdate,
        "content": "", "link": "https://www.dealabs.com/bons-plans/" + key,
    }


class PriceSignalsTests(unittest.TestCase):
    def test_explicit_single_euro_price_only(self):
        self.assertEqual(signals.headline_price("Samsung SSD à 59,99 € - offre"), 59.99)
        self.assertEqual(signals.headline_price("Lampada 29 EUR"), 29)
        self.assertIsNone(signals.headline_price("349 € au lieu de 799 €"))
        self.assertIsNone(signals.headline_price("RTX 5080 -70%"))
        self.assertIsNone(signals.headline_price("coût de 2 €"))

    def test_error_signals_multilingual(self):
        for phrase in ("erreur de prix", "bug de prix", "error de precio",
                       "errore di prezzo", "price glitch", "pricing error"):
            with self.subTest(phrase=phrase):
                self.assertTrue(signals.ERROR_RE.search(phrase))

    def test_no_baseline_from_same_post_or_duplicate_scans(self):
        base = 1_900_000_000
        previous = [{"post": "A", "price": 250, "at": base - 96 * 3600},
                    {"post": "A", "price": 260, "at": base - 48 * 3600}]
        self.assertIsNone(signals.reference_drop(previous, 50, "B", base))
        previous[1]["post"] = "B"
        self.assertIsNone(signals.reference_drop(previous, 50, "B", base))

    def test_requires_baseline_span_and_age(self):
        now = 1_900_000_000
        close = [{"post": "A", "price": 200, "at": now - 6 * 3600},
                 {"post": "B", "price": 200, "at": now - 4 * 3600}]
        self.assertIsNone(signals.reference_drop(close, 35, "C", now))
        old_but_close = [{"post": "A", "price": 200, "at": now - 60 * 3600},
                         {"post": "B", "price": 200, "at": now - 59 * 3600}]
        self.assertIsNone(signals.reference_drop(old_but_close, 35, "C", now))

    def test_recognizes_large_publication_drop_not_amazon_price(self):
        now = 1_900_000_000
        old = [{"post": "first", "price": 240, "at": now - 96 * 3600},
               {"post": "second", "price": 200, "at": now - 48 * 3600}]
        drop = signals.reference_drop(old, 59, "third", now)
        self.assertIsNotNone(drop)
        self.assertEqual(drop["previous_posts"], 2)
        self.assertEqual(drop["reference"], 220)
        self.assertGreater(drop["decrease_percent"], 70)
        self.assertIsNone(signals.reference_drop(old, 199, "third", now))

    def test_record_one_observation_per_distinct_post(self):
        history = {}
        signals.record_observation(history, "https://amazon.fr/dp/B0B9C4DKKG",
                                   "post1", 200, 100)
        signals.record_observation(history, "https://amazon.fr/dp/B0B9C4DKKG",
                                   "post1", 2, 999)
        self.assertEqual(len(history["https://amazon.fr/dp/B0B9C4DKKG"]), 1)
        self.assertEqual(history["https://amazon.fr/dp/B0B9C4DKKG"][0]["price"], 200)

    def test_price_watch_requires_brand_and_direct_link(self):
        item = post("Samsung SSD promotion 79 €",
                    "https://www.amazon.fr/dp/B0B9C4DKKG")
        with patch.object(bot, "ENABLE_EARLY_DISCOVERY", True), \
             patch.object(bot, "fetch", side_effect=AssertionError("unexpected fetch")):
            found = bot.candidates([item])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["signal"], "price_watch")
        self.assertIsNone(found[0]["public_baseline"])
        self.assertTrue(found[0]["amazon"].startswith("https://amazon.fr/dp/"))

    def test_stated_error_has_priority_without_stated_discount(self):
        error = post("RTX 5080 erreur de prix 349 €",
                     "https://www.amazon.fr/dp/B0B9C4DKKG", "glitch")
        normal = post("LEGO Technic -60%",
                      "https://www.amazon.fr/dp/B0B9C4DKKH", "normal")
        with patch.object(bot, "ENABLE_EARLY_DISCOVERY", True), \
             patch.object(bot, "fetch", side_effect=AssertionError("unexpected fetch")):
            found = bot.candidates([normal, error])
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0]["signal"], "suspected_error")
        self.assertEqual(found[0]["discount"], 0)

    def test_publication_history_allows_price_only_no_promo(self):
        now = datetime.now(timezone.utc)
        item = post("Samsung SSD 49 €", "https://amazon.fr/dp/B0B9C4DKKG")
        prev = [
            {"post": "early", "price": 240, "at": int((now-timedelta(days=5)).timestamp())},
            {"post": "later", "price": 200, "at": int((now-timedelta(days=3)).timestamp())},
        ]
        with patch.object(bot, "ENABLE_EARLY_DISCOVERY", True), \
             patch.object(bot, "fetch", side_effect=AssertionError("unexpected fetch")):
            found = bot.candidates([item], {"https://amazon.fr/dp/B0B9C4DKKG": prev})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["signal"], "historical_offer_drop")
        self.assertGreater(found[0]["public_baseline"]["decrease_percent"], 70)

    def test_caps_watch_and_urgent_and_no_fake_discount(self):
        posts = [
            post(f"Samsung SSD promotion {70+i} €",
                 f"https://www.amazon.fr/dp/B0B9C4D{'KKG' if i==0 else 'KKH' if i==1 else 'KKJ'}",
                 f"watch-{i}") for i in range(4)
        ]
        with tempfile.TemporaryDirectory() as td, \
             patch.object(bot, "STATE_FILE", Path(td) / "seen.json"), \
             patch.object(bot, "DRY_RUN", True), \
             patch.object(bot, "ENABLE_EARLY_DISCOVERY", True), \
             patch.object(bot, "MAX_NOTIFICATIONS", 8), \
             patch.object(bot, "MAX_PRICE_WATCH", 2), \
             patch.object(bot, "collect", return_value=(posts, 1, 1)):
            self.assertEqual(bot.run(), 2)
            self.assertEqual(bot.run(), 0)


if __name__ == "__main__":
    unittest.main()
