"""Auditable price provenance: announced prices are not live Amazon prices.

Records changed prices from deal posts separately from optional Keepa
market observations. Both are keyed by exact source or marketplace+ASIN.
"""
from datetime import datetime, timezone, timedelta
import sqlite3
import pricechecks

SCHEMA = """
CREATE TABLE IF NOT EXISTS public_price_changes (
  source_url TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  advertised_price_eur REAL NOT NULL,
  PRIMARY KEY(source_url,recorded_at)
);
CREATE INDEX IF NOT EXISTS idx_changes_source ON public_price_changes(source_url);
CREATE TABLE IF NOT EXISTS keepa_observations (
  market TEXT NOT NULL,
  asin TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  current_eur REAL NOT NULL,
  reference_90d_eur REAL,
  drop_90d_percent REAL,
  price_type TEXT NOT NULL,
  PRIMARY KEY(market,asin,observed_at,price_type)
);
CREATE INDEX IF NOT EXISTS idx_keepa_index
  ON keepa_observations(market,asin,observed_at DESC);
"""


def ensure(db):
    db.executescript(SCHEMA)


def record_public_changes(rows, db_path, now=None):
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as db:
        ensure(db)
        new = 0
        for row in rows:
            if row.get("price_scope") in ("amazon_creators_featured_new", "keepa_observed_new", "brightdata_search_listing"):
                # Creators API offer data must not enter the 60-day public
                # post-price history. A 23h expiring cache is in seen.json.
                continue
            amount = row.get("advertised_price_eur")
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
                continue
            last = db.execute(
                "SELECT advertised_price_eur FROM public_price_changes "
                "WHERE source_url=? ORDER BY recorded_at DESC LIMIT 1",
                (row["source_url"],)
            ).fetchone()
            if last is not None and abs(last[0] - amount) < .009:
                continue
            db.execute(
                "INSERT OR IGNORE INTO public_price_changes VALUES (?,?,?)",
                (row["source_url"], now, round(amount, 2))
            )
            new += 1
        db.execute("DELETE FROM public_price_changes WHERE source_url NOT IN "
                   "(SELECT source_url FROM offers)")
        db.execute("DELETE FROM keepa_observations WHERE observed_at < ?",
                   ((datetime.now(timezone.utc) - timedelta(days=100)).isoformat(timespec="seconds"),))
        db.commit()
    return new


def record_keepa(observations, db_path):
    if not observations:
        return 0
    saved = 0
    with sqlite3.connect(db_path) as db:
        ensure(db)
        for item in observations:
            if item.get("source") != "Keepa":
                continue
            market, asin = item.get("market"), item.get("asin")
            if not pricechecks.identify(f"https://{market}/dp/{asin}"):
                continue
            db.execute(
                "INSERT OR REPLACE INTO keepa_observations VALUES (?,?,?,?,?,?,?)",
                (market, asin, item["observed_at"], item["current_eur"],
                 item.get("reference_90d_eur"),
                 item.get("drop_90d_percent"), item["price_type"])
            )
            saved += 1
        db.commit()
    return saved


def enrich(row, db, now=None):
    """Attach secondary data for JSON and HTML; never override RSS headline."""
    now = now or datetime.now(timezone.utc)
    changes = db.execute(
        "SELECT recorded_at,advertised_price_eur FROM public_price_changes "
        "WHERE source_url=? ORDER BY recorded_at DESC LIMIT 12",
        (row["source_url"],)
    ).fetchall()
    row["public_price_changes"] = [
        {"recorded_at": r[0], "advertised_price_eur": r[1]} for r in changes
    ]
    row["keepa"] = None
    identity = pricechecks.identify(row.get("amazon_product_url"))
    if identity:
        obs = db.execute(
            "SELECT observed_at,current_eur,reference_90d_eur,drop_90d_percent,price_type "
            "FROM keepa_observations WHERE market=? AND asin=? "
            "ORDER BY observed_at DESC LIMIT 1", identity
        ).fetchone()
        if obs:
            when = datetime.fromisoformat(obs[0])
            age = (now - when).total_seconds()
            if -600 <= age <= 48 * 3600:
                row["keepa"] = {
                    "observed_at": obs[0], "current_eur": obs[1],
                    "reference_90d_eur": obs[2], "drop_90d_percent": obs[3],
                    "price_type": obs[4], "source": "Keepa",
                    "shipping_verified": False, "seller_verified": False,
                }
    return row
