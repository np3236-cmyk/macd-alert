#!/usr/bin/env python3
"""
BTCUSDT MACD Crossover Check — single-run version for GitHub Actions.
"""

import os
import json
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

SYMBOL = "BTCUSDT"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
FAST, SLOW, SIGNAL = 12, 26, 9
MATCH_WINDOW_MINUTES = 10
LOOKBACK_CANDLES = 10

STATE_FILE = "state.json"
IST = timezone(timedelta(hours=5, minutes=30))

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def in_alert_window_ist() -> bool:
    now_ist = datetime.now(timezone.utc).astimezone(IST)
    start = now_ist.replace(hour=10, minute=0, second=0, microsecond=0)
    end = now_ist.replace(hour=22, minute=0, second=0, microsecond=0)
    return start <= now_ist <= end


def fetch_klines(interval: str, limit: int = 100):
    params = {"symbol": SYMBOL, "interval": interval, "limit": limit}
    r = requests.get(BINANCE_KLINES_URL, params=params, timeout=10)
    r.raise_for_status()
    raw = r.json()
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "taker_base", "taker_quote", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    now = datetime.now(timezone.utc)
    if df.iloc[-1]["close_time"].to_pydatetime() > now:
        df = df.iloc[:-1].reset_index(drop=True)
    return df


def compute_macd(df: pd.DataFrame):
    close = df["close"]
    ema_fast = close.ewm(span=FAST, adjust=False).mean()
    ema_slow = close.ewm(span=SLOW, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=SIGNAL, adjust=False).mean()
    return macd_line, signal_line


def detect_latest_crossover(df: pd.DataFrame):
    if len(df) < SLOW + SIGNAL + 2:
        return None, None
    macd_line, signal_line = compute_macd(df)
    found = None
    start = max(1, len(df) - LOOKBACK_CANDLES)
    for i in range(start, len(df)):
        pm, ps = macd_line.iloc[i - 1], signal_line.iloc[i - 1]
        lm, ls = macd_line.iloc[i], signal_line.iloc[i]
        if pm <= ps and lm > ls:
            found = ("bullish", df["close_time"].iloc[i])
        elif pm >= ps and lm < ls:
            found = ("bearish", df["close_time"].iloc[i])
    return found if found else (None, None)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            raw = json.load(f)
        for key in ("last_1m", "last_5m"):
            if raw.get(key, {}).get("time"):
                raw[key]["time"] = datetime.fromisoformat(raw[key]["time"])
        return raw
    return {
        "last_1m": {"direction": None, "time": None},
        "last_5m": {"direction": None, "time": None},
        "last_alert_signature": None,
    }


def save_state(state):
    out = json.loads(json.dumps(state, default=str))
    with open(STATE_FILE, "w") as f:
        json.dump(out, f, indent=2, default=lambda o: o.isoformat())


def send_telegram_alert(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": False,
    }
    r = requests.post(url, data=payload, timeout=10)
    r.raise_for_status()


def main():
    state = load_state()

    d1, t1 = detect_latest_crossover(fetch_klines("1m"))
    if d1 and (state["last_1m"]["time"] is None or t1 > state["last_1m"]["time"]):
        state["last_1m"] = {"direction": d1, "time": t1}
        print(f"[1m] {d1} crossover confirmed at {t1}")

    d5, t5 = detect_latest_crossover(fetch_klines("5m"))
    if d5 and (state["last_5m"]["time"] is None or t5 > state["last_5m"]["time"]):
        state["last_5m"] = {"direction": d5, "time": t5}
        print(f"[5m] {d5} crossover confirmed at {t5}")

    l1, l5 = state["last_1m"], state["last_5m"]
    if l1["direction"] and l5["direction"] and l1["direction"] == l5["direction"]:
        gap = abs((l1["time"] - l5["time"]).total_seconds()) / 60
        if gap <= MATCH_WINDOW_MINUTES:
            signature = f"{l1['direction']}|{l1['time']}|{l5['time']}"
            if signature != state.get("last_alert_signature"):
                direction = l1["direction"]
                if in_alert_window_ist():
                    emoji = "🟢" if direction == "bullish" else "🔴"
                    msg = (
                        f"{emoji} <b>{SYMBOL} MACD {direction.upper()} Crossover</b>\n\n"
                        f"1m confirmed: {l1['time'].astimezone(IST).strftime('%H:%M:%S IST')}\n"
                        f"5m confirmed: {l5['time'].astimezone(IST).strftime('%H:%M:%S IST')}\n\n"
                        f"Both timeframes aligned on {direction} crossover."
                    )
                    send_telegram_alert(msg)
                    print(f"ALERT SENT: {direction} match")
                else:
                    print(f"Match found ({direction}) but outside 10AM-10PM IST — suppressed.")
                state["last_alert_signature"] = signature

    save_state(state)


if __name__ == "__main__":
    main()
