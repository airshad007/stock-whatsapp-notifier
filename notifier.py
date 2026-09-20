#!/usr/bin/env python3
"""
Stock price -> Telegram notifier.

Usage:
    python notifier.py send                 # fetch prices and send the twice-daily Telegram update
    python notifier.py send --dry-run        # print the message instead of sending it
    python notifier.py check                 # send an alert only for tickers that moved past the threshold today
    python notifier.py check --threshold 2.0  # use a custom threshold (percent, default 1.0)
    python notifier.py check --dry-run        # print any alert message instead of sending it
    python notifier.py add TSLA               # add a ticker to the dynamic watchlist
    python notifier.py remove TSLA             # remove a ticker from the watchlist
    python notifier.py list                    # print the current watchlist

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
ALERT_STATE_FILE = SCRIPT_DIR / "alert_state.json"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
DEFAULT_ALERT_THRESHOLD = 5.0

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
# Alert state (which tickers have already fired an alert today, so a ticker
# that stays past the threshold all day only pings you once, not every time
# the checker runs). Stored in alert_state.json and committed back to the
# repo by the GitHub Actions workflow, since Actions runners are ephemeral.
# --------------------------------------------------------------------------

def _today_str() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def load_alert_state() -> dict:
    if not ALERT_STATE_FILE.exists():
        return {"date": _today_str(), "alerted": {}}
    with open(ALERT_STATE_FILE, "r") as f:
        state = json.load(f)
    if state.get("date") != _today_str():
        # New day - everyone gets a clean slate.
        state = {"date": _today_str(), "alerted": {}}
    return state


def save_alert_state(state: dict) -> None:
    with open(ALERT_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


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

def _format_table(prices: list[dict]) -> str:
    symbol_width = max(len(p["symbol"]) for p in prices)
    lines = []
    for p in prices:
        up = p["change_pct"] >= 0
        arrow = "\U0001F7E2" if up else "\U0001F534"  # green/red circle
        sign = "+" if up else ""
        lines.append(
            f"{p['symbol']:<{symbol_width}}  ${p['price']:>8.2f}  {arrow} {sign}{p['change_pct']:.2f}%"
        )
    return "\n".join(lines)


def format_message(prices: list[dict]) -> str:
    now = datetime.now(LOCAL_TZ).strftime("%b %d, %I:%M %p %Z")
    header = f"<b>\U0001F4C8 Stock Update</b>\n<i>{now}</i>"

    if not prices:
        return f"{header}\n\nNo data available."

    return f"{header}\n\n<pre>{_format_table(prices)}</pre>"


def format_alert_message(prices: list[dict], threshold: float) -> str:
    now = datetime.now(LOCAL_TZ).strftime("%b %d, %I:%M %p %Z")
    header = f"<b>\U0001F6A8 Price Alert</b> (±{threshold:g}%)\n<i>{now}</i>"
    return f"{header}\n\n<pre>{_format_table(prices)}</pre>"


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
# Threshold-based alert check - meant to be run frequently (e.g. every 15
# minutes during market hours) by its own GitHub Actions workflow, separate
# from the twice-daily "send" schedule. Each ticker only alerts once per
# calendar day the first time it crosses +/-threshold%, so this can't spam
# you even if it stays past the threshold for the rest of the session.
# --------------------------------------------------------------------------

def check_and_alert(threshold: float, dry_run: bool) -> None:
    tickers = load_tickers()
    if not tickers:
        print("Watchlist is empty - nothing to check.")
        return

    state = load_alert_state()
    alerted = state["alerted"]

    prices = fetch_prices(tickers)
    newly_triggered = [
        p for p in prices
        if abs(p["change_pct"]) >= threshold and p["symbol"] not in alerted
    ]

    if not newly_triggered:
        print(f"No new tickers crossed +/-{threshold:g}% (already alerted today: "
              f"{', '.join(sorted(alerted)) or 'none'}).")
        return

    message = format_alert_message(newly_triggered, threshold)
    if dry_run:
        print(message)
        return

    send_telegram_message(message)
    for p in newly_triggered:
        alerted[p["symbol"]] = p["change_pct"]
    state["alerted"] = alerted
    save_alert_state(state)


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

    p_check = sub.add_parser(
        "check",
        help="Send an alert only for tickers that just crossed the move threshold (once per ticker per day)",
    )
    p_check.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_ALERT_THRESHOLD,
        help=f"Percent move (up or down) from previous close that triggers an alert (default {DEFAULT_ALERT_THRESHOLD})",
    )
    p_check.add_argument("--dry-run", action="store_true", help="Print any alert message instead of sending it")

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
    elif args.command == "check":
        check_and_alert(args.threshold, args.dry_run)


if __name__ == "__main__":
    main()
