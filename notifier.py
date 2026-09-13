#!/usr/bin/env python3
"""
Stock price -> WhatsApp notifier, using Meta's WhatsApp Cloud API directly
(no Twilio or other middleman).

Usage:
    python notifier.py send              # fetch prices and send a WhatsApp message
    python notifier.py send --freeform   # send as a plain-text message instead of a
                                          # template (only works inside an open 24h
                                          # WhatsApp "customer service window" - i.e.
                                          # you must have messaged the business number
                                          # from your phone in the last 24h). Useful for
                                          # quick manual testing.
    python notifier.py add TSLA          # add a ticker to the dynamic watchlist
    python notifier.py remove TSLA       # remove a ticker from the watchlist
    python notifier.py list              # print the current watchlist

Watchlist is stored in tickers.json next to this script, so you can also just
open that file and edit the list by hand at any time.

Required environment variables (see .env.example):
    WHATSAPP_PHONE_NUMBER_ID   - the "Phone number ID" of your WhatsApp Cloud API sender
    WHATSAPP_ACCESS_TOKEN      - a permanent (System User) access token, NOT the 24h temp token
    WHATSAPP_TEMPLATE_NAME     - name of your approved message template (see README)
    WHATSAPP_TEMPLATE_LANG     - template language code, e.g. en_US
    WHATSAPP_TO_NUMBER         - your own WhatsApp number in E.164 digits, no "+", e.g. 14155551234
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
TICKERS_FILE = SCRIPT_DIR / "tickers.json"
GRAPH_API_VERSION = "v21.0"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

load_dotenv(SCRIPT_DIR / ".env")


# --------------------------------------------------------------------------
# Watchlist management (dynamic, file-backed)
# --------------------------------------------------------------------------

def load_tickers() -> list[str]:
    if not TICKERS_FILE.exists():
        return []
    with open(TICKERS_FILE, "r") as f:
        data = json.load(f)
    return data.get("tickers", [])


def save_tickers(tickers: list[str]) -> None:
    with open(TICKERS_FILE, "w") as f:
        json.dump({"tickers": tickers}, f, indent=2)
        f.write("\n")


def add_ticker(symbol: str) -> None:
    symbol = symbol.strip().upper()
    tickers = load_tickers()
    if symbol in tickers:
        print(f"{symbol} is already on the watchlist.")
        return
    tickers.append(symbol)
    save_tickers(tickers)
    print(f"Added {symbol}. Watchlist is now: {', '.join(tickers)}")


def remove_ticker(symbol: str) -> None:
    symbol = symbol.strip().upper()
    tickers = load_tickers()
    if symbol not in tickers:
        print(f"{symbol} is not on the watchlist.")
        return
    tickers.remove(symbol)
    save_tickers(tickers)
    print(f"Removed {symbol}. Watchlist is now: {', '.join(tickers) if tickers else '(empty)'}")


def list_tickers() -> None:
    tickers = load_tickers()
    print(", ".join(tickers) if tickers else "(watchlist is empty)")


# --------------------------------------------------------------------------
# Price fetching
# --------------------------------------------------------------------------

def fetch_prices(tickers: list[str]) -> list[dict]:
    """Returns a list of {symbol, price, change_pct} dicts. Skips tickers that fail."""
    results = []
    for symbol in tickers:
        try:
            t = yf.Ticker(symbol)
            fast = t.fast_info
            price = fast.get("last_price") or fast.get("lastPrice")
            prev_close = fast.get("previous_close") or fast.get("previousClose")
            if price is None or prev_close is None:
                hist = t.history(period="2d")
                if hist.empty or len(hist) < 2:
                    raise ValueError("insufficient history data")
                price = float(hist["Close"].iloc[-1])
                prev_close = float(hist["Close"].iloc[-2])
            change_pct = ((price - prev_close) / prev_close) * 100 if prev_close else 0.0
            results.append({"symbol": symbol, "price": price, "change_pct": change_pct})
        except Exception as exc:  # noqa: BLE001 - we want to keep going for other tickers
            print(f"WARNING: failed to fetch {symbol}: {exc}", file=sys.stderr)
    return results


# --------------------------------------------------------------------------
# Message formatting
#
# NOTE: WhatsApp template parameters cannot contain newline/tab characters or
# more than 4 consecutive spaces, so the whole report is built as a single
# line with a separator, rather than one stock per line.
# --------------------------------------------------------------------------

def format_message(prices: list[dict]) -> str:
    now = datetime.now(LOCAL_TZ).strftime("%b %d, %I:%M %p %Z")
    parts = []
    for p in prices:
        arrow = "+" if p["change_pct"] >= 0 else ""
        parts.append(f"{p['symbol']} ${p['price']:.2f} ({arrow}{p['change_pct']:.2f}%)")
    body = " | ".join(parts) if parts else "no data"
    return f"Stock update ({now}): {body}"


# --------------------------------------------------------------------------
# Sending via Meta's WhatsApp Cloud API directly
# --------------------------------------------------------------------------

def _graph_url() -> str:
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    return f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"


def _headers() -> dict:
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _recipients() -> list[str]:
    """WHATSAPP_TO_NUMBERS is a comma-separated list of E.164 digits (no '+').
    Falls back to the older single-number WHATSAPP_TO_NUMBER for compatibility."""
    raw = os.environ.get("WHATSAPP_TO_NUMBERS") or os.environ.get("WHATSAPP_TO_NUMBER", "")
    return [n.strip() for n in raw.split(",") if n.strip()]


def send_template_message(body_text: str) -> None:
    """Send using an approved template - required for proactive/scheduled messages
    sent outside a 24h customer-service window, which a twice-a-day cron job always is."""
    template_name = os.environ["WHATSAPP_TEMPLATE_NAME"]
    template_lang = os.environ.get("WHATSAPP_TEMPLATE_LANG", "en_US")

    for to_number in _recipients():
        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": template_lang},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": body_text}],
                    }
                ],
            },
        }
        _post(payload)


def send_freeform_message(body_text: str) -> None:
    """Plain text message. Only deliverable if the recipient messaged this WhatsApp
    business number within the last 24 hours (an open customer-service window).
    Handy for manual testing; not reliable for an unattended scheduled job."""
    for to_number in _recipients():
        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": body_text},
        }
        _post(payload)


def _post(payload: dict) -> None:
    resp = requests.post(_graph_url(), headers=_headers(), json=payload, timeout=20)
    if resp.status_code >= 400:
        print(f"WhatsApp API error {resp.status_code}: {resp.text}", file=sys.stderr)
        resp.raise_for_status()
    print("Message sent:", resp.json())


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Print the current watchlist")

    p_add = sub.add_parser("add", help="Add a ticker to the watchlist")
    p_add.add_argument("symbol")

    p_remove = sub.add_parser("remove", help="Remove a ticker from the watchlist")
    p_remove.add_argument("symbol")

    p_send = sub.add_parser("send", help="Fetch prices and send the WhatsApp update")
    p_send.add_argument(
        "--freeform",
        action="store_true",
        help="Send as plain text instead of an approved template (only works within an open 24h window)",
    )
    p_send.add_argument("--dry-run", action="store_true", help="Print the message instead of sending it")

    args = parser.parse_args()

    if args.command == "list":
        list_tickers()
    elif args.command == "add":
        add_ticker(args.symbol)
    elif args.command == "remove":
        remove_ticker(args.symbol)
    elif args.command == "send":
        tickers = load_tickers()
        if not tickers:
            print("Watchlist is empty - nothing to send.")
            return
        prices = fetch_prices(tickers)
        message = format_message(prices)
        if args.dry_run:
            print(message)
            return
        if args.freeform:
            send_freeform_message(message)
        else:
            send_template_message(message)


if __name__ == "__main__":
    main()
