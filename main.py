import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import requests

BINANCE_URL = "https://fapi.binance.com"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT"]

CHECK_INTERVAL_SECONDS = 60
OI_ALERT_THRESHOLD = 5.0  # percent

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError(
        "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variable."
    )

# Keep enough 1-minute observations for 15-minute comparisons.
history = defaultdict(lambda: deque(maxlen=30))
last_alert_bucket = {}  # prevents repeating the same 5m/15m alert every minute


def get_json(path, params):
    r = requests.get(BINANCE_URL + path, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def get_market_data(symbol):
    oi_data = get_json("/fapi/v1/openInterest", {"symbol": symbol})
    price_data = get_json("/fapi/v1/ticker/price", {"symbol": symbol})

    return float(oi_data["openInterest"]), float(price_data["price"])


def pct_change(current, old):
    if old == 0:
        return 0.0
    return ((current - old) / old) * 100.0


def classify(oi_change, price_change):
    # Standard OI + price interpretation.
    if oi_change > 0 and price_change > 0:
        return "🟢 Long buildup"
    if oi_change > 0 and price_change < 0:
        return "🔴 Short buildup"
    if oi_change < 0 and price_change > 0:
        return "🟡 Short covering"
    if oi_change < 0 and price_change < 0:
        return "🟠 Long liquidation"
    return "⚪ Mixed / flat"


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
        timeout=15,
    )
    r.raise_for_status()


def check_symbol(symbol):
    oi, price = get_market_data(symbol)
    now = time.time()
    history[symbol].append((now, oi, price))

    # Find observations at least 5 and 15 minutes old.
    for minutes in (5, 15):
        target_age = minutes * 60
        old = None

        # Pick the observation closest to the requested age, but not newer.
        candidates = [
            item for item in history[symbol]
            if item[0] <= now - target_age
        ]
        if candidates:
            old = min(candidates, key=lambda item: abs(item[0] - (now - target_age)))

        if old is None:
            continue

        old_ts, old_oi, old_price = old
        oi_change = pct_change(oi, old_oi)
        price_change = pct_change(price, old_price)

        if abs(oi_change) < OI_ALERT_THRESHOLD:
            continue

        # One alert per symbol/timeframe per minute bucket.
        bucket = int(now // (minutes * 60))
        alert_key = (symbol, minutes, bucket)
        if last_alert_bucket.get((symbol, minutes)) == bucket:
            continue
        last_alert_bucket[(symbol, minutes)] = bucket

        classification = classify(oi_change, price_change)
        direction = "📈 OI increased" if oi_change > 0 else "📉 OI decreased"

        message = (
            f"🚨 Binance OI Alert\n\n"
            f"Symbol: {symbol}\n"
            f"Timeframe: {minutes}m\n"
            f"{direction}: {oi_change:+.2f}%\n"
            f"Price change: {price_change:+.2f}%\n"
            f"Signal: {classification}\n\n"
            f"Current OI: {oi:,.2f}\n"
            f"Current price: {price:,.6f}\n"
            f"UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
        )

        try:
            send_telegram(message)
            print(message.replace("\n", " | "), flush=True)
        except Exception as e:
            print(f"Telegram error: {e}", flush=True)


def main():
    print("Binance OI Telegram bot started.", flush=True)
    print(f"Symbols: {', '.join(SYMBOLS)}", flush=True)
    print(f"Check interval: {CHECK_INTERVAL_SECONDS}s", flush=True)
    print(f"OI alert threshold: ±{OI_ALERT_THRESHOLD}%", flush=True)

    # Send a startup message so we know Railway is running the bot.
    try:
        send_telegram(
            "✅ Binance OI Alert Bot is online.\n"
            "Monitoring BTCUSDT, ETHUSDT and XRPUSDT every 1 minute."
        )
    except Exception as e:
        print(f"Startup Telegram error: {e}", flush=True)

    while True:
        cycle_start = time.time()

        for symbol in SYMBOLS:
            try:
                check_symbol(symbol)
            except Exception as e:
                print(f"{symbol} error: {e}", flush=True)

        elapsed = time.time() - cycle_start
        time.sleep(max(1, CHECK_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    main()
