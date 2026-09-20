#!/usr/bin/env python3
"""
Stock price -> Telegram notifier.

Usage:
    python notifier.py send              # fetch prices and send a Telegram message
    python notifier.py send --dry-run    # print the message instead of sending it
    python notifier.py add TSLA          # add a ticker to the dynamic watchlist
    python notifier.py remove TSLA       # remove a ticker from the watchlist
    python notifier.py list              # print the current watchlist

Watchlist is stored in tickers.json next to this script, so you can also just
open that file (or edit it on GitHub) and change the list by hand at any time.

Required environment variables (see .env.example):
    TELEGRAM_BOT_TOKEN  - token for the bot you create via @BotFather
    TELEGRAM_CHAT_ID    - the chat id sendMessage should deliver to (your own chat with the bot)
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
        except Exception as exc:  # noqa: BLE001 - keep going for other tickers
            print(f"WARNING: failed to fetch {symbol}: {exc}", file=sys.stderr)
    return results


# --------------------------------------------------------------------------
# Message formatting - a "card": title/timestamp, then one aligned line per
# ticker inside a monospace block. Telegram has no line-break restriction
# (that was a WhatsApp-template-only limitation), so this renders as a clean
# little table in the chat.
# --------------------------------------------------------------------------

def format_message(prices: list[dict]) -> str:
    now = datetime.now(LOCAL_TZ).strftime("%b %d, %I:%M %p %Z")
    header = f"<b>\U0001F4C8 Stock Update</b>\n<i>{now}</i>"

    if not prices:
        return f"{header}\n\nNo data available."

    symbol_width = max(len(p["symbol"]) for p in prices)
    lines = []
    for p in prices:
        up = p["change_pct"] >= 0
        arrow = "\U0001F7E2" if up else "\U0001F534"  # green/red circle
        sign = "+" if up else ""
        lines.append(
            f"{p['symbol']:<{symbol_width}}  ${p['price']:>8.2f}  {arrow} {sign}{p['change_pct']:.2f}%"
        )
    table = "\n".join(lines)
    return f"{header}\n\n<pre>{table}</pre>"


# --------------------------------------------------------------------------
# Sending via Telegram's Bot API
# --------------------------------------------------------------------------

def send_telegram_message(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    resp = requests.post(url, json=payload, timeout=20)
    if resp.status_code >= 400:
        print(f"Telegram API error {resp.status_code}: {resp.text}", file=sys.stderr)
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

    p_send = sub.add_parser("send", help="Fetch prices and send the Telegram update")
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
        send_telegram_message(message)


if __name__ == "__main__":
    main()
