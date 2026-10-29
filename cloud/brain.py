# -*- coding: utf-8 -*-
"""
مغز ابری ربات سرخط‌زن — نسخه ابری برای اجرای بدون کامپیوتر
============================================================
معماری (الهام‌گرفته از ربات‌های حرفه‌ای مثل نمونه Polymarket):

    Watcher  →  Brain  →  Executor  →  Guard
    (دیده‌بان)   (مغز)      (مجری)      (نگهبان)

  • Watcher:  گرفتن دیتای واقعی — کندل ۵ دقیقه‌ای بیت‌کوین (نوبیتکس /
              Coinbase / CoinGecko) و قیمت لحظه‌ای بورس تهران (TSETMC)
  • Brain:    پیش‌بینی جهت ۵ دقیقه‌ای بعدی بیت‌کوین (UP/DOWN) با امتیاز
              اعتماد ۰ تا ۱۰۰ + استراتژی شکست محدوده برای بورس
  • Executor: ثبت معامله شبیه‌سازی (پیپر) — بدون پول واقعی
  • Guard:    سقف معامله روزانه، سقف ضرر روز، استراخت باخت متوالی،
              حداقل امتیاز اعتماد

اجرا:
    python cloud/brain.py --data-dir webapp/data

خروجی:
    webapp/data/live.json        ← اپ موبایل این فایل را می‌خواند
    webapp/data/brain_state.json ← حافظه کاری مغز (کندل‌ها/دفتر معاملات)

روی GitHub Actions هر ۵ دقیقه اجرا می‌شود و اگر چیزی عوض شده باشد،
نتیجه را منتشر می‌کند؛ اپ گوشی همیشه آخرین وضعیت را می‌بیند.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
from core.market_data import NobitexData, TseData, DataError
from core.signal_engine import SignalEngine

# ============================================================
#  پارامترهای استراتژی ۵ دقیقه‌ای بیت‌کوین
# ============================================================
WINDOW_SEC = 300              # پنجره ۵ دقیقه‌ای (مثل ربات Polymarket)
MIN_CONFIDENCE = 60           # زیر این امتیاز: صبر (WAIT)
MIN_CONF_BET = 62             # حداقل امتیاز برای باز کردن شرط‌بندی شبیه‌سازی
PAYOUT = 0.85                 # سود شرط درست (۸۵٪) — مثل بازارهای دوتایی
MAX_BETS_PER_DAY = 15         # نگهبان: حداکثر شرط در روز
DAILY_LOSS_CAP_PCT = 0.08     # نگهبان: توقف روزانه بعد از ۸٪ ضرر
LOSS_STREAK_COOLDOWN = 3      # نگهبان: ۳ باخت متوالی → ۱۵ دقیقه استراحت
COOLDOWN_SEC = 15 * 60
STAKE_PCT = 0.01              # ۱٪ موجودی در هر شرط

HTTP_UA = {"User-Agent": "sarkhat-bot/2.0 (+github-actions)"}


# ============================================================
#  ابزار شبکه (فقط کتابخانه استاندارد — بدون هیچ وابستگی)
# ============================================================
def http_json(url, timeout=12, headers=None):
    req = urllib.request.Request(url, headers={**HTTP_UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


# ============================================================
#  WATCHER — دیده‌بان دیتا
# ============================================================
def fetch_btc_candles():
    """
    کندل ۵ دقیقه‌ای بیت‌کوین — سه منبع پشتیبان:
      ۱) نوبیتکس (تومان — داخل ایران / Termux)
      ۲) Coinbase (دلار — سرورهای خارج، برای GitHub Actions)
      ۳) CoinGecko (دلار — پشتیبان نهایی)
    خروجی: (لیست کندل صعودیِ زمانی، نام منبع، واحد)
    کندل‌ها فقط «بسته‌شده» هستند (کندل در حال شکل‌گیری حذف می‌شود).
    """
    now = int(time.time())

    # ---------- ۱) نوبیتکس ----------
    try:
        candles = NobitexData.get_candles("BTCIRT", resolution=5, days=1)
        closed = [c for c in candles if c["time"] + WINDOW_SEC <= now]
        if len(closed) >= 40:
            return [dict(c) for c in closed[-240:]], "نوبیتکس", "IRT"
    except Exception:
        pass

    # ---------- ۲) Coinbase ----------
    try:
        rows = http_json(
            "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=300",
            timeout=10)
        # هر سطر: [time, low, high, open, close, volume] — جدیدترین اول
        candles = sorted(
            ({"time": int(r[0]), "open": float(r[3]), "high": float(r[2]),
              "low": float(r[1]), "close": float(r[4]), "volume": float(r[5])}
             for r in rows), key=lambda c: c["time"])
        closed = [c for c in candles if c["time"] + WINDOW_SEC <= now]
        if len(closed) >= 40:
            return closed[-240:], "Coinbase", "USD"
    except Exception:
        pass

    # ---------- ۳) CoinGecko ----------
    try:
        data = http_json(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
            "?vs_currency=usd&days=1", timeout=12)
        buckets = {}
        for ts, px in data.get("prices", []):
            b = int(ts) // WINDOW_SEC * WINDOW_SEC
            buckets.setdefault(b, []).append(float(px))
        candles = []
        for b in sorted(buckets):
            pxs = buckets[b]
            candles.append({"time": b, "open": pxs[0], "high": max(pxs),
                            "low": min(pxs), "close": pxs[-1], "volume": 0.0})
        closed = [c for c in candles if c["time"] + WINDOW_SEC <= now]
        if len(closed) >= 40:
            return closed[-240:], "CoinGecko", "USD"
    except Exception:
        pass

    return None, None, None


# ============================================================
#  BRAIN — مغز پیش‌بینی جهت ۵ دقیقه‌ای
# ============================================================
def ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def compute_btc_signal(candles):
    """
    پیش‌بینی جهت پنجره ۵ دقیقه‌ای بعدی.
    ۵ فاکتور وزن‌دار → امتیاز بین -۱ تا +۱ → اعتماد ۵۰ تا ۹۵
    منطق دقیقاً همین در اپ گوشی (btc.js) پیاده شده تا هر دو هم‌راستا باشند.
    """
    closes = [c["close"] for c in candles]
    n = len(closes)
    if n < 30:
        return None
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, n)]

    mom1 = rets[-1]
    mom3 = sum(rets[-3:])
    e9, e21 = ema(closes, 9), ema(closes, 21)
    ema_gap = (e9 - e21) / e21 if (e9 and e21) else 0.0
    r = rsi(closes, 14)
    base_vol = sum(abs(x) for x in rets[-20:]) / 20 or 1e-9
    now_vol = sum(abs(x) for x in rets[-6:]) / 6
    vol_regime = now_vol / base_vol
    # موقعیت قیمت داخل باند ۲۰ کندلی (بازگشت به میانگین در اوج‌ها)
    sma20 = sum(closes[-20:]) / 20
    var20 = sum((c - sma20) ** 2 for c in closes[-20:]) / 20
    sd20 = var20 ** 0.5 or 1e-9
    bb_pos = clamp((closes[-1] - sma20) / (2 * sd20), -1.5, 1.5)

    s_mom1 = clamp(mom1 / 0.003, -1, 1)
    s_mom3 = clamp(mom3 / 0.006, -1, 1)
    s_ema = clamp(ema_gap / 0.0015, -1, 1)
    s_rsi = clamp((r - 50) / 25, -1, 1)
    s_bb = -clamp(bb_pos / 1.2, -1, 1)          # برخلاف اوج/قعر — بازگشت به میانگین

    score = (0.22 * s_mom1 + 0.20 * s_mom3 + 0.22 * s_ema +
             0.16 * s_rsi + 0.20 * s_bb)
    score = clamp(score, -1, 1)

    confidence = round(50 + abs(score) * 45)
    note = ""
    if vol_regime > 2.5:
        confidence = round(confidence * 0.9)
        note = "نوسان غیرعادی — اعتماد کم شد"

    direction = "UP" if score >= 0 else "DOWN"
    action = direction
    if abs(score) < 0.12 or confidence < MIN_CONFIDENCE:
        action = "WAIT"
        if not note:
            note = "سیگنال ضعیف — صبر"

    return {
        "direction": direction,
        "action": action,
        "confidence": int(confidence),
        "features": {
            "mom1_pct": round(mom1 * 100, 3),
            "mom3_pct": round(mom3 * 100, 3),
            "ema_gap_pct": round(ema_gap * 100, 3),
            "rsi": round(r, 1),
            "vol_regime": round(vol_regime, 2),
            "bb_pos": round(bb_pos, 2),
        },
        "note": note,
    }


# ============================================================
#  EXECUTOR + GUARD — مجری شبیه‌سازی و نگهبان ریسک
# ============================================================
def new_book():
    return {
        "start_equity": 1000.0,
        "equity": 1000.0,
        "open": None,
        "bets": [],
        "day": {"date": "", "trades": 0, "wins": 0, "pnl": 0.0},
        "loss_streak": 0,
        "cooldown_until": 0,
    }


def roll_day(book):
    """ریست روزانه به وقت تهران (نیمه‌شب تهران)"""
    today = TseData.tehran_now_label()[:10]
    if book["day"]["date"] != today:
        book["day"] = {"date": today, "trades": 0, "wins": 0, "pnl": 0.0}


def guard_allow(book, signal):
    """نگهبان — آیا اجازه شرط جدید هست؟ خروجی: (bool، دلیل)"""
    if book["open"]:
        return False, "پنجره باز داریم"
    if signal["action"] == "WAIT" or signal["confidence"] < MIN_CONF_BET:
        return False, "اعتماد کم"
    if book["day"]["trades"] >= MAX_BETS_PER_DAY:
        return False, "سقف معامله روزانه پر شد"
    if book["day"]["pnl"] <= -book["start_equity"] * DAILY_LOSS_CAP_PCT:
        return False, "سقف ضرر روز — تا فردا توقف"
    if time.time() < book["cooldown_until"]:
        return False, "استراحت بعد از باخت‌های متوالی"
    return True, ""


def settle_open_bet(book, candles):
    """تسویه شرط باز اگر پنجره‌اش بسته شده باشد. خروجی: پیام تلگرام یا None"""
    bet = book["open"]
    if not bet:
        return None
    match = next((c for c in candles if c["time"] == bet["window_start"]), None)
    if match is None:
        return None
    end_px = match["close"]
    if end_px == bet["entry"]:
        result, pnl = "push", 0.0
    else:
        went_up = end_px > bet["entry"]
        win = went_up if bet["dir"] == "UP" else (not went_up)
        result = "win" if win else "loss"
        pnl = bet["stake"] * PAYOUT if win else -bet["stake"]
    book["equity"] = round(book["equity"] + pnl, 2)
    book["day"]["pnl"] = round(book["day"]["pnl"] + pnl, 2)
    book["loss_streak"] = 0 if result in ("win", "push") else book["loss_streak"] + 1
    if book["loss_streak"] >= LOSS_STREAK_COOLDOWN:
        book["cooldown_until"] = time.time() + COOLDOWN_SEC
        book["loss_streak"] = 0
    bet.update({"end_price": end_px, "result": result, "pnl": round(pnl, 2),
                "closed_at": TseData.tehran_now_label()})
    book["bets"].append(bet)
    book["bets"] = book["bets"][-60:]
    book["open"] = None
    icon = {"win": "✅ برد", "loss": "❌ باخت", "push": "➖ مساوی"}[result]
    return (f"{icon} | بیت‌کوین {bet['dir']}\n"
            f"ورود: {bet['entry']:,.0f} → پایان: {end_px:,.0f}\n"
            f"سود/زیان: {pnl:+.1f} از {bet['stake']:.0f} واحد\n"
            f"موجودی شبیه‌سازی: {book['equity']:,.0f}")


def maybe_open_bet(book, signal, candles, unit):
    """شرط جدید شبیه‌سازی برای پنجره جاری (تا ۶۰ ثانیه بعد از شروع پنجره)"""
    now = time.time()
    t0 = (int(now) // WINDOW_SEC) * WINDOW_SEC
    if now - t0 > 60:
        return None, None
    allowed, why = guard_allow(book, signal)
    if not allowed:
        return None, why
    prev = next((c for c in candles if c["time"] == t0 - WINDOW_SEC), None)
    if prev is None:
        return None, "کندل شروع پیدا نشد"
    stake = round(book["equity"] * STAKE_PCT, 2)
    bet = {
        "window_start": t0,
        "dir": signal["direction"],
        "entry": prev["close"],
        "stake": stake,
        "confidence": signal["confidence"],
        "opened_at": TseData.tehran_now_label(),
    }
    book["open"] = bet
    book["day"]["trades"] += 1
    msg = (f"🎯 شرط شبیه‌سازی جدید | بیت‌کوین {signal['direction']}\n"
           f"اعتماد: {signal['confidence']}٪ | ورود: {bet['entry']:,.0f} {unit}\n"
           f"پنجره: ۵ دقیقه | مبلغ: {stake:.0f}")
    return bet, msg


# ============================================================
#  اسکن بورس تهران (همان موتور ۸ فاکتوری — با حافظه ماندگار)
# ============================================================
def scan_tse_signals(state, engine, daily_cache):
    """کندل ۱۵ دقیقه‌ای از قیمت لحظه‌ای TSETMC ساخته می‌شود (حافظه در state)"""
    today = TseData.tehran_now_label()[:10]
    out = []
    for symbol in config.TSE_SYMBOLS:
        try:
            code = TseData.resolve_symbol(symbol)
            rt = TseData.get_realtime(code)
            local = time.gmtime(time.time() + TseData.TEHRAN_UTC_OFFSET)
            bucket = (local.tm_hour * 60 + local.tm_min) // 15 * 15
            bucket_key = f"{bucket // 60:02d}:{bucket % 60:02d}"
            bars_map = state.setdefault("tse_bars", {}).setdefault(symbol, {})
            bars = bars_map.setdefault(today, [])
            cum = rt["volume"]
            prev_cum = state.get("tse_last_cum", {}).get(symbol, 0)
            bar_vol = max(cum - prev_cum, 0) if prev_cum else 0
            state.setdefault("tse_last_cum", {})[symbol] = cum

            price = rt["last"] or rt["close"]
            if not bars or bars[-1]["bucket"] != bucket_key:
                bars.append({"bucket": bucket_key, "time": int(time.time()),
                             "open": price, "high": price, "low": price,
                             "close": price, "volume": bar_vol})
            else:
                b = bars[-1]
                b["high"] = max(b["high"], price)
                b["low"] = min(b["low"], price)
                b["close"] = price
                b["volume"] += bar_vol
            bars_map[today] = bars[-30:]
            for d in list(bars_map):
                if d != today:
                    del bars_map[d]

            plain = [{k: v for k, v in b.items() if k != "bucket"} for b in bars_map[today]]
            idx = 0.0
            try:
                ic = TseData.resolve_symbol("شاخص کل")
                irt = TseData.get_realtime(ic)
                idx = (irt["last"] / irt["yclose"] - 1) * 100 if irt["yclose"] else 0.0
            except Exception:
                pass
            hit = daily_cache.get(symbol)
            if not hit or time.time() - hit[0] > 6 * 3600:
                try:
                    hit = (time.time(), TseData.get_daily_history(symbol, days=120))
                except Exception:
                    hit = (time.time(), None)
                daily_cache[symbol] = hit
            sig = engine.evaluate(symbol, "tse", plain, daily_candles=hit[1],
                                  index_change=idx, spread_pct=None,
                                  time_label=TseData.tehran_now_label(),
                                  day_label=today)
            d = sig.to_dict()
            d["last"] = price
            d["day_change_pct"] = (price / rt["yclose"] - 1) * 100 if rt.get("yclose") else 0.0
            out.append(d)
        except Exception as e:
            out.append({"symbol": symbol, "market": "tse", "rejected": True,
                        "reject_reason": f"خطای دیتا: {e.__class__.__name__}",
                        "time": TseData.tehran_now_label(),
                        "session_day": today, "grade": "-", "score": 0,
                        "factors": [], "action": "hold"})
    return out


def scan_crypto_signals(engine):
    """سیگنال شکست ۱۵ دقیقه‌ای کریپتو (فقط وقتی نوبیتکس در دسترس است)"""
    today = time.strftime("%Y-%m-%d")
    tz = 3.5 * 3600
    midnight = (int(time.time()) + tz) // 86400 * 86400 - tz
    out = []
    for symbol in config.CRYPTO_SYMBOLS:
        try:
            candles = NobitexData.get_candles(symbol, 15, days=3)
            session = [c for c in candles if c["time"] >= midnight]
            if len(session) <= config.RANGE_BARS:
                session = candles[-30:]
            sig = engine.evaluate(symbol, "crypto", session, daily_candles=None,
                                  index_change=0.0, spread_pct=None,
                                  time_label=TseData.tehran_now_label(),
                                  day_label=today)
            out.append(sig.to_dict())
        except Exception:
            pass
    return out


# ============================================================
#  تلگرام (اختیاری — با Secrets گیت‌هاب)
# ============================================================
def tg_send(text):
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat:
        return False
    try:
        url = ("https://api.telegram.org/bot" + token + "/sendMessage?" +
               urllib.parse.urlencode({"chat_id": chat, "text": text}))
        urllib.request.urlopen(urllib.request.Request(url, headers=HTTP_UA), timeout=10)
        return True
    except Exception:
        return False


def dedup_and_notify(state, key, uid, text):
    """هر رویداد فقط یک‌بار اعلان شود"""
    seen = state.setdefault("notified", {}).setdefault(key, [])
    if uid in seen:
        return
    seen.append(uid)
    state["notified"][key] = seen[-200:]
    tg_send(text)


# ============================================================
#  MAIN — یک دور کامل مغز ابری
# ============================================================
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def dump_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def content_fingerprint(live):
    """اثر انگشت محتوا — بدون فیلدهای لحظه‌ای (برای اینکه هر دور کامیت الکی نزنیم)"""
    slim = {k: v for k, v in live.items()
            if k not in ("updated_at", "tehran_time")}
    slim["brain"] = {k: v for k, v in slim.get("brain", {}).items()
                     if k != "last_run"}
    raw = json.dumps(slim, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run(data_dir, force_changed=False):
    os.makedirs(data_dir, exist_ok=True)
    live_path = os.path.join(data_dir, "live.json")
    state_path = os.path.join(data_dir, "brain_state.json")

    prev_live = load_json(live_path, {})
    state = load_json(state_path, {"run_count": 0})
    state["run_count"] = state.get("run_count", 0) + 1

    engine = SignalEngine(range_bars=config.RANGE_BARS,
                          volume_mult=config.VOLUME_MULT,
                          break_buffer=config.BREAK_BUFFER,
                          stop_loss_pct=config.STOP_LOSS_PCT,
                          take_profit_rr=config.TAKE_PROFIT_RR)
    daily_cache = {}

    tse_open = TseData.is_market_open()

    # ---------- WATCHER: بیت‌کوین ----------
    candles, btc_source, btc_unit = fetch_btc_candles()
    btc_signal = compute_btc_signal(candles) if candles else None

    # ---------- EXECUTOR + GUARD ----------
    book = state.get("book") or new_book()
    book["start_equity"] = book.get("start_equity", 1000.0)
    roll_day(book)
    settle_msg = None
    bet_msg = None
    if candles and btc_signal:
        settle_msg = settle_open_bet(book, candles)
        bet, bet_msg = maybe_open_bet(book, btc_signal, candles, btc_unit)
        if not bet and bet_msg:
            pass  # دلیل باز نشدن در state می‌ماند
    state["book"] = book

    # ---------- WATCHER + BRAIN: بورس تهران ----------
    tse_signals = prev_live.get("signals", {}).get("tse", [])
    tse_reachable = None
    if tse_open:
        fresh = scan_tse_signals(state, engine, daily_cache)
        ok_count = sum(1 for s in fresh if "خطای دیتا" not in (s.get("reject_reason") or ""))
        tse_reachable = ok_count > 0
        if tse_reachable:
            tse_signals = fresh
            # اعلان تلگرام برای سیگنال معتبر تازه
            for s in fresh:
                if (not s.get("rejected") and s.get("grade") in ("A", "B")
                        and s.get("action") == "buy"):
                    dedup_and_notify(
                        state, "tse", f"{s['symbol']}|{s.get('session_day')}|{s.get('time')}",
                        f"🎯 سیگنال {s['grade']} بورس — {s['symbol']}\n"
                        f"امتیاز: {s.get('score')}\nورود: {s.get('entry'):,.0f} | "
                        f"ضرر: {s.get('stop'):,.0f} | سود: {s.get('target'):,.0f}")
    elif tse_open is False and prev_live.get("signals", {}).get("tse"):
        # بازار بسته — سیگنال‌های آخرین جلسه حفظ می‌شود
        tse_reachable = prev_live.get("brain", {}).get("tse_reachable")

    # ---------- کریپتو ۱۵ دقیقه‌ای (فقط با نوبیتکس — داخل ایران) ----------
    crypto_signals = prev_live.get("signals", {}).get("crypto", [])
    if btc_source == "نوبیتکس":
        fresh = scan_crypto_signals(engine)
        if fresh:
            crypto_signals = fresh

    # ---------- اعلان‌های شرط بیت‌کوین ----------
    if settle_msg:
        dedup_and_notify(state, "btc_settle",
                         f"{book['bets'][-1]['window_start']}", settle_msg)
    if bet_msg and btc_signal and btc_signal["confidence"] >= 70:
        dedup_and_notify(state, "btc_open",
                         f"open-{book['open']['window_start']}" if book["open"] else "open-?",
                         bet_msg)

    # ---------- قیمت تومانی بیت‌کوین (اگر نوبیتکس جواب داد) ----------
    price_irt = None
    if btc_source == "نوبیتکس":
        try:
            price_irt = NobitexData.get_prices(["BTCIRT"])["BTCIRT"]["last"]
        except Exception:
            price_irt = None

    # ---------- سرهم‌بندی خروجی اپ ----------
    closed = candles[-1]["close"] if candles else None
    btc_block = {
        **(btc_signal or {}),
        "unit": btc_unit,
        "price": closed,
        "price_irt": price_irt,
        "source": btc_source,
        "window_start": (int(time.time()) // WINDOW_SEC) * WINDOW_SEC,
        "window_sec": WINDOW_SEC,
        "payout_pct": PAYOUT * 100,
    }
    wins = len([b for b in book["bets"] if b.get("result") == "win"])
    losses = len([b for b in book["bets"] if b.get("result") == "loss"])
    live = {
        "version": 2,
        "updated_at": TseData.tehran_now_label(),
        "tehran_time": TseData.tehran_now_label(),
        "tse_open": tse_open,
        "demo": False,
        "signals": {"tse": tse_signals[-40:], "crypto": crypto_signals[-40:]},
        "btc": btc_block,
        "btc_paper": {
            "start_equity": book["start_equity"],
            "equity": book["equity"],
            "open_bet": book["open"],
            "bets": book["bets"][-20:],
            "today": book["day"],
            "stats": {"total": wins + losses, "wins": wins, "losses": losses,
                      "win_rate": round(wins / (wins + losses) * 100, 1) if wins + losses else None},
        },
        "brain": {
            "run_count": state["run_count"],
            "last_run": TseData.tehran_now_label(),
            "tse_reachable": tse_reachable,
            "btc_source": btc_source,
        },
    }

    # ---------- ذخیره + تشخیص تغییر ----------
    fp = content_fingerprint(live)
    changed = force_changed or (fp != state.get("last_fp"))
    state["last_fp"] = fp
    dump_json(state_path, state)
    dump_json(live_path, live)

    with open(os.path.join(data_dir, ".changed"), "w") as f:
        f.write("1" if changed else "0")

    print(f"brain run #{state['run_count']} | btc_source={btc_source} "
          f"| conf={btc_signal['confidence'] if btc_signal else '-'} "
          f"| action={btc_signal['action'] if btc_signal else '-'} "
          f"| tse_open={tse_open} | tse_reachable={tse_reachable} | changed={changed}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="مغز ابری سرخط‌زن")
    ap.add_argument("--data-dir", default=os.path.join(ROOT, "webapp", "data"))
    ap.add_argument("--force-changed", action="store_true")
    args = ap.parse_args(argv)
    try:
        return run(args.data_dir, force_changed=args.force_changed)
    except Exception as e:
        print(f"FATAL brain: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
