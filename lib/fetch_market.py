#!/usr/bin/env python3
"""Fetch exchange rates from ExchangeRate-API and emit to tmp/market.txt."""

from __future__ import annotations

import os
import shutil
import urllib.request

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
TMP_DIR = os.path.join(ROOT_DIR, "tmp")
TMP_STATE_DIR = os.path.join(TMP_DIR, "state")

OUTFILE = os.path.join(TMP_DIR, "market.txt")
LAST_CACHE = os.path.join(TMP_STATE_DIR, ".market_last_success.txt")

REQUEST_TIMEOUT = 8.0
USER_AGENT = "soren-market-fetcher/1.0"

API_BASE = "https://api.exchangerate-api.com/v4/latest"


def ensure_dirs() -> None:
    os.makedirs(TMP_DIR, exist_ok=True)
    os.makedirs(TMP_STATE_DIR, exist_ok=True)


def http_get_json(url: str) -> dict:
    import json
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def write_text(path: str, text: str) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp_path, path)


def safe_unlink(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def restore_cache() -> bool:
    if not os.path.exists(LAST_CACHE):
        return False
    shutil.copyfile(LAST_CACHE, OUTFILE)
    return True


def fetch_rates() -> dict[str, float] | None:
    """Fetch USD/JPY, EUR/JPY, GBP/JPY rates."""
    try:
        usd_data = http_get_json(f"{API_BASE}/USD")
        usd_rates = usd_data.get("rates", {})
        jpy_per_usd = usd_rates.get("JPY")
        gbp_per_usd = usd_rates.get("GBP")
        if jpy_per_usd is None:
            return None

        result = {"USD/JPY": round(jpy_per_usd, 2)}

        # GBP/JPY from cross rate
        if gbp_per_usd and gbp_per_usd > 0:
            result["GBP/JPY"] = round(jpy_per_usd / gbp_per_usd, 2)
    except Exception:
        return None

    # EUR/JPY from separate call
    try:
        eur_data = http_get_json(f"{API_BASE}/EUR")
        eur_rates = eur_data.get("rates", {})
        jpy_per_eur = eur_rates.get("JPY")
        if jpy_per_eur is not None:
            result["EUR/JPY"] = round(jpy_per_eur, 2)
    except Exception:
        pass  # EUR failure is non-fatal

    return result if result else None


def render(rates: dict[str, float]) -> str:
    lines = ["【為替レート】"]
    for pair in ("USD/JPY", "EUR/JPY", "GBP/JPY"):
        if pair in rates:
            lines.append(f"{pair}: {rates[pair]}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ensure_dirs()

    rates = fetch_rates()
    if rates:
        text = render(rates)
        write_text(OUTFILE, text)
        write_text(LAST_CACHE, text)
        return 0

    # API failed — try cache fallback
    if restore_cache():
        return 0

    # Total failure — remove output so caller knows
    safe_unlink(OUTFILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
