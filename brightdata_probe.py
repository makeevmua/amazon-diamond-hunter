"""Bounded, opt-in one-search Bright Data integration probe.

This is a one-time billable discovery probe: one Amazon.FR keyword page only.
Never prints account keys, full URLs with tokens, or raw provider responses.
"""
import os
from urllib.error import HTTPError, URLError

import brightdata_search


def main():
    token = os.getenv("BRIGHTDATA_API_TOKEN", "")
    if not token:
        print("BRIGHT LIVE PROBE: token missing in GitHub Actions.")
        return 0
    try:
        products = brightdata_search._read_search(
            token, "amazon.fr", "SSD NVMe", timeout=75
        )
    except HTTPError as error:
        code = error.code
        cases = {
            400: "request schema or dataset unsupported",
            401: "authentication rejected",
            402: "account plan or credits required",
            403: "permission denied to selected dataset",
            404: "dataset unavailable",
            429: "provider quota/rate limit",
        }
        print(f"BRIGHT LIVE PROBE: HTTP {code}; "
              f"{cases.get(code, 'provider returned an error')}.")
        return 0
    except (URLError, TimeoutError, ValueError, OSError) as error:
        print(f"BRIGHT LIVE PROBE: {type(error).__name__}; "
              "provider access not verified.")
        return 0
    parsed = [r for r in products if brightdata_search.normalize(r)]
    print(f"BRIGHT LIVE PROBE: provider returned {len(products)} rows; "
          f"{len(parsed)} eligible Amazon EU price records.")
    if products and not parsed:
        print("BRIGHT LIVE PROBE: data present but parser rejected all rows; "
              "check timestamp, region, EUR currency, ASIN and policy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
