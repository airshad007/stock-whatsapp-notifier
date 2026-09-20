# Stock price → Telegram notifier

Sends a Telegram message twice a day with current prices for a watchlist you
control, formatted as a clean little card. Runs for free on GitHub Actions -
no dependence on your laptop being on.

## 1. Create your Telegram bot (2 minutes, no approval process)

1. Install Telegram on your iPhone (free, App Store) if you don't have it.
2. In Telegram, search for **@BotFather** and start a chat with it.
3. Send `/newbot`, give it a name and a username when prompted (username must
   end in "bot", e.g. `aamir_stock_bot`).
4. BotFather replies with a **token** that looks like `123456789:AAExample...`
   - this is your `TELEGRAM_BOT_TOKEN`.
5. Search for the bot you just created (by the username you gave it) and send
   it any message, e.g. "hi" - this is required once so the bot knows who to
   reply to.
6. In a browser, visit:
   `https://api.telegram.org/bot<your-token>/getUpdates`
   (replacing `<your-token>` with your actual token). Look for `"chat":{"id":
   ...}` in the JSON - that number is your `TELEGRAM_CHAT_ID`.

## 2. Push to GitHub and set secrets

```bash
cd ~/Documents/stock-whatsapp-notifier
git add notifier.py requirements.txt tickers.json .gitignore .github .env.example README.md
git commit -m "Switch to Telegram"
git push
```

In the repo's **Settings → Secrets and variables → Actions**, add:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

(You can delete the old `WHATSAPP_*` secrets and the `stock_update` /
`stock_watchlist_alert` templates in Meta's WhatsApp Manager - no longer
needed.)

## 3. Test it

From the repo's **Actions** tab, open "Stock Telegram Notifier" and click
**Run workflow** to trigger it immediately rather than waiting for the
schedule. Check Telegram for the message.

Locally, you can also test with:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your real token and chat id
python3 notifier.py send --dry-run   # just prints the message
python3 notifier.py send             # sends for real
```

## Managing your watchlist

Edit `tickers.json` directly (in GitHub's web UI, or locally with `git pull`,
edit, `git commit`, `git push`), or use the CLI:
```bash
python3 notifier.py list
python3 notifier.py add TSLA
python3 notifier.py remove TSLA
```

## What the message looks like

```
📈 Stock Update
Sep 20, 6:45 AM PDT

META   $716.24  🟢 +1.02%
NOW    $940.12  🔴 -0.34%
NVDA   $187.55  🟢 +2.10%
GOOGL  $198.30  🟢 +0.85%
AMZN   $225.40  🔴 -0.12%
AVGO   $305.20  🟢 +1.75%
```

## Notes

- Prices come from Yahoo Finance via the `yfinance` library - free, no API
  key, ~15-minute delayed data (not real-time).
- No approval process, no message categories, no templates - Telegram's Bot
  API just sends whatever you send it.
