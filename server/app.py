# -*- coding: utf-8 -*-
"""
سرور سبک ربات سرخط‌زن — فقط با کتابخانه استاندارد پایتون
=========================================================
این فایل سه کار می‌کند:
  ۱) اپلیکیشن موبایل/مرورگر (پوشه webapp) را سرو می‌کند
  ۲) API JSON می‌دهد تا اپ، سیگنال‌ها و قیمت‌ها را ببیند
  ۳) در پس‌زمینه هر چند دقیقه بازار را اسکن و سیگنالِ معتبر می‌سازد
     و اگر «پیپرتریدینگ» روشن باشد، معامله شبیه‌سازی ثبت می‌کند

اجرا روی گوشی (Termux):  python main.py serve
اجرا روی کامپیوتر:       python main.py serve --port 8000
"""
import json
import os
import sys
import threading
import time
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.market_data import NobitexData, TseData, DataError
from core.signal_engine import SignalEngine
from core.paper_broker import PaperBroker
from core.risk_manager import RiskManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP_DIR = os.path.join(ROOT, "webapp")
DATA_DIR = os.path.join(ROOT, "data")
VERSION = "2.0.0"

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon",
        ".webmanifest": "application/manifest+json; charset=utf-8"}


# ============================================================
#  حالت نمایشی (فقط وقتی هیچ سرور واقعی در دسترس نیست)
# ============================================================
class DemoFeed:
    """
    اگر ربات بیرون از ایران اجرا شود (TSETMC و نوبیتکس هر دو مسدود)،
    ربات به‌جای خوابیدن، داده «نمایشی» می‌سازد تا رفتار برنامه دیده شود.
    ⚠️ این داده واقعی نیست و در اپ با برچسب «نمایشی» مشخص می‌شود.
    با متغیر محیطی SARKHAT_ALLOW_DEMO=0 می‌توانید کلاً خاموشش کنید.
    """
    _seed_prices = {"BTCIRT": 8_500_000_000, "ETHIRT": 280_000_000,
                    "USDTIRT": 88_500, "فولاد": 150_000, "خودرو": 42_000, "شپنا": 96_000}

    @classmethod
    def candles(cls, symbol, n=90):
        rnd = random.Random(hash(symbol) % 99999)
        base = cls._seed_prices.get(symbol, 100_000)
        price = base * (0.9 + rnd.random() * 0.2)
        bars = []
        t0 = int(time.time()) - n * 900
        vol = max(base / 200_000, 2) * 500
        for i in range(n):
            drift = rnd.gauss(0, 0.0015)
            spike = False
            if i > 40 and rnd.random() < 0.10:
                drift = abs(rnd.gauss(0.006, 0.003))    # پرش واقعی‌نما
                spike = True
            o = price
            c = price * (1 + drift)
            h = max(o, c) * (1 + rnd.random() * 0.002)
            l = min(o, c) * (1 - rnd.random() * 0.002)
            v = vol * (3.0 if spike else (0.5 + rnd.random()))
            bars.append({"time": t0 + i * 900, "open": o, "high": h,
                         "low": l, "close": c, "volume": v})
            price = c
        return bars

    @classmethod
    def price(cls, symbol):
        rnd = random.Random(int(time.time() // 30) + hash(symbol) % 999)
        base = cls._seed_prices.get(symbol, 100_000)
        p = base * (1 + rnd.gauss(0, 0.01))
        return {"last": p, "dayHigh": p * 1.02, "dayLow": p * 0.97,
                "dayOpen": base, "dayChange": (p / base - 1) * 100,
                "bestBuy": p * 0.999, "bestSell": p * 1.001, "isClosed": False}


# ============================================================
#  اسکنر پس‌زمینه
# ============================================================
class Scanner:
    """هر SCAN_INTERVAL ثانیه بازار را اسکن می‌کند و سیگنال/معامله می‌سازد"""

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        TseData._codes_file = os.path.join(DATA_DIR, "ins_codes.json")

        self.lock = threading.Lock()
        self.engine = SignalEngine(range_bars=config.RANGE_BARS,
                                   volume_mult=config.VOLUME_MULT,
                                   break_buffer=config.BREAK_BUFFER,
                                   stop_loss_pct=config.STOP_LOSS_PCT,
                                   take_profit_rr=config.TAKE_PROFIT_RR)
        self.settings = self._load_settings()
        self.state = self._load_state()

        self.demo = os.environ.get("SARKHAT_ALLOW_DEMO", "1") != "0"
        self.reachable = {"tse": None, "crypto": None}   # None=نامشخص True/False
        self.tse_bars = {}      # symbol -> {day: [bars]}  کندل‌های ۱۵ دقیقه‌ای ساخته‌شده از لحظه‌ای
        self.tse_last_cum = {}  # symbol -> حجم تجمعی آخرین بار
        self.daily_cache = {}   # symbol -> (ts, candles)
        self.started = time.time()
        self.last_scan = 0
        self.scan_count = 0

        # بروکر پیپر + ریسک برای هر بازار
        self.brokers = {
            "tse": PaperBroker(capital=config.CAPITAL, fee=config.FEE_TSE,
                               slippage=config.SLIPPAGE, log_dir=DATA_DIR,
                               log_name="trades_tse.csv"),
            "crypto": PaperBroker(capital=config.CAPITAL, fee=config.FEE_CRYPTO,
                                  slippage=config.SLIPPAGE, log_dir=DATA_DIR,
                                  log_name="trades_crypto.csv"),
        }
        self.risks = {
            "tse": RiskManager(config.CAPITAL, config.RISK_PER_TRADE,
                               config.MAX_DAILY_LOSS, config.MAX_TRADES_PER_DAY,
                               config.MAX_POSITION_PCT),
            "crypto": RiskManager(config.CAPITAL, config.RISK_PER_TRADE,
                                  config.MAX_DAILY_LOSS, config.MAX_TRADES_PER_DAY,
                                  config.MAX_POSITION_PCT),
        }

    # ------------------------------------------------------
    def _settings_path(self):
        return os.path.join(DATA_DIR, "settings.json")

    def _load_settings(self):
        default = {
            "scan_interval_sec": 90,          # هر ۹۰ ثانیه اسکن (کم‌مصرف)
            "min_grade": "B",                 # فقط سیگنال A و B وارد معامله پیپر شوند
            "auto_paper": True,               # معامله شبیه‌سازی خودکار
            "tse_symbols": list(config.TSE_SYMBOLS),
            "crypto_symbols": list(config.CRYPTO_SYMBOLS),
            "low_data": True,                 # کش طولانی‌تر و درخواست کمتر
        }
        try:
            with open(self._settings_path(), "r", encoding="utf-8") as f:
                default.update(json.load(f))
        except (OSError, ValueError):
            pass
        return default

    def save_settings(self, new=None):
        if new:
            self.settings.update(new)
        with open(self._settings_path(), "w", encoding="utf-8") as f:
            json.dump(self.settings, f, ensure_ascii=False, indent=1)
        return self.settings

    def _state_path(self):
        return os.path.join(DATA_DIR, "state.json")

    def _load_state(self):
        try:
            with open(self._state_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"signals": {"tse": [], "crypto": []}, "notes": ""}

    def save_state(self):
        try:
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False)
        except OSError:
            pass

    # ------------------------------------------------------
    def _daily(self, symbol):
        """دیتای روزانه با کش ۶ ساعته (کم‌مصرف)"""
        hit = self.daily_cache.get(symbol)
        if hit and time.time() - hit[0] < 6 * 3600:
            return hit[1]
        try:
            candles = TseData.get_daily_history(symbol, days=120)
            self.daily_cache[symbol] = (time.time(), candles)
            return candles
        except DataError:
            return None

    def _index_change_tse(self):
        """درصد تغییر شاخص کل بورس امروز (اگر در دسترس بود)"""
        try:
            code = TseData.resolve_symbol("شاخص کل")
            rt = TseData.get_realtime(code)
            y = rt["yclose"]
            return (rt["last"] / y - 1) * 100 if y > 0 else 0.0
        except (DataError, ZeroDivisionError):
            return 0.0

    def _index_change_crypto(self):
        """تغییر ۲۴ ساعته بیت‌کوین به‌عنوان دماسنج بازار کریپتو"""
        try:
            p = NobitexData.get_prices(["BTCIRT"])
            return p["BTCIRT"]["dayChange"]
        except (DataError, KeyError):
            return 0.0

    # ------------------------------------------------------
    def scan_crypto(self, symbol):
        """اسکن یک ارز با دیتای واقعی ۱۵ دقیقه‌ای نوبیتکس"""
        today = time.strftime("%Y-%m-%d")
        candles = NobitexData.get_candles(symbol, 15, days=3)
        # کندل‌های «امروز» به وقت تهران
        tz = 3.5 * 3600
        midnight = (int(time.time()) + tz) // 86400 * 86400 - tz
        session = [c for c in candles if c["time"] >= midnight]
        if len(session) <= config.RANGE_BARS:
            session = candles[-30:]     # بازار ۲۴ ساعته است؛ از آخرین کندل‌ها استفاده کن
        spread = self._crypto_spread(symbol)
        idx = self._index_change_crypto()
        return self.engine.evaluate(symbol, "crypto", session,
                                    daily_candles=None, index_change=idx,
                                    spread_pct=spread,
                                    time_label=TseData.tehran_now_label(),
                                    day_label=today)

    def _crypto_spread(self, symbol):
        try:
            ob = NobitexData.get_orderbook(symbol)
            if ob["asks"] and ob["bids"]:
                mid = (ob["asks"][0][0] + ob["bids"][0][0]) / 2
                return (ob["asks"][0][0] - ob["bids"][0][0]) / mid if mid > 0 else None
        except DataError:
            pass
        return None

    # ------------------------------------------------------
    def scan_tse(self, symbol):
        """اسکن یک نماد بورس — کندل ۱۵ دقیقه‌ای از قیمت لحظه‌ای ساخته می‌شود"""
        today = TseData.tehran_now_label()[:10]
        code = TseData.resolve_symbol(symbol)
        rt = TseData.get_realtime(code)

        # ساخت کندل ۱۵ دقیقه‌ای در حافظه
        local = time.gmtime(time.time() + TseData.TEHRAN_UTC_OFFSET)
        bucket = (local.tm_hour * 60 + local.tm_min) // 15 * 15
        bucket_key = f"{bucket // 60:02d}:{bucket % 60:02d}"
        day_bars = self.tse_bars.setdefault(symbol, {}).setdefault(today, [])
        cum = rt["volume"]
        prev_cum = self.tse_last_cum.get(symbol, 0)
        bar_vol = max(cum - prev_cum, 0) if prev_cum else 0
        self.tse_last_cum[symbol] = cum

        price = rt["last"] or rt["close"]
        if not day_bars or day_bars[-1]["bucket"] != bucket_key:
            day_bars.append({"bucket": bucket_key, "time": int(time.time()),
                             "open": price, "high": price, "low": price,
                             "close": price, "volume": bar_vol})
        else:
            b = day_bars[-1]
            b["high"] = max(b["high"], price)
            b["low"] = min(b["low"], price)
            b["close"] = price
            b["volume"] += bar_vol

        # فقط ۳۰ کندل آخر نگه می‌داریم (حافظه کم)
        self.tse_bars[symbol][today] = day_bars[-30:]
        bars = [{k: v for k, v in b.items() if k != "bucket"} for b in self.tse_bars[symbol][today]]

        idx = self._index_change_tse()
        daily = self._daily(symbol)
        return self.engine.evaluate(symbol, "tse", bars, daily_candles=daily,
                                    index_change=idx, spread_pct=None,
                                    time_label=TseData.tehran_now_label(),
                                    day_label=today)

    # ------------------------------------------------------
    def _paper_trade(self, market, sig):
        """ثبت معامله شبیه‌سازی روی سیگنال معتبر + مدیریت پوزیشن باز"""
        if not self.settings.get("auto_paper"):
            return
        broker = self.brokers[market]
        risk = self.risks[market]
        today = sig.session_day or time.strftime("%Y-%m-%d")
        if risk.state.day != today:
            risk.start_new_day(today)
        grades = {"A": 3, "B": 2, "C": 1}
        min_level = grades.get(self.settings.get("min_grade", "B"), 2)

        # مدیریت پوزیشن باز (بررسی سقف/کف سیگنال با قیمت فعلی)
        if broker.position_qty > 0:
            price = sig.entry
            pnl = None
            if sig.stop and price <= sig.entry * (1 - config.STOP_LOSS_PCT * 0.5):
                pnl = broker.sell(broker.position_qty, sig.stop, sig.time, "حد ضرر")
            elif sig.target and price >= sig.target * 0.995:
                pnl = broker.sell(broker.position_qty, sig.target, sig.time, "حد سود")
            if pnl is not None:
                risk.register_trade_result(pnl or 0)
            return

        # ورود جدید فقط روی سیگنال با درجه کافی
        if not sig.is_valid or sig.rejected:
            return
        if grades.get(sig.grade, 0) < min_level:
            return
        allowed, why = risk.can_open(broker.invested_value)
        if not allowed:
            return
        qty, _ = risk.position_size(sig.entry, sig.stop)
        if qty > 0 and broker.buy(qty, sig.entry, sig.time, f"سیگنال {sig.grade} — {sig.symbol}"):
            self._append_note(f"معامله شبیه‌سازی ثبت شد: {sig.symbol} درجه {sig.grade}")

    def _append_note(self, text):
        ts = TseData.tehran_now_label()
        self.state["notes"] = f"[{ts}] {text}"

    # ------------------------------------------------------
    def scan_once(self):
        """یک دور کامل اسکن همه نمادها"""
        results = {"tse": [], "crypto": []}
        tse_open = TseData.is_market_open()

        # ---------- کریپتو ----------
        for symbol in self.settings["crypto_symbols"]:
            try:
                sig = self.scan_crypto(symbol)
                results["crypto"].append(sig)
                self.reachable["crypto"] = True
            except DataError as e:
                self.reachable["crypto"] = False
                results["crypto"].append(self._error_signal(symbol, "crypto", str(e)))

        # ---------- بورس ----------
        for symbol in self.settings["tse_symbols"]:
            try:
                if tse_open:
                    sig = self.scan_tse(symbol)
                    self.reachable["tse"] = True
                else:
                    sig = self._closed_market_signal(symbol)
                results["tse"].append(sig)
            except DataError as e:
                self.reachable["tse"] = False
                results["tse"].append(self._error_signal(symbol, "tse", str(e)))

        # ---------- حالت نمایشی (فقط وقتی هیچ سرور واقعی جواب نمی‌دهد) ----------
        if (self.reachable["crypto"] is not True and self.reachable["tse"] is not True
                and self.demo and not tse_open):
            self.state["demo"] = True
            for symbol in self.settings["crypto_symbols"][:2]:
                bars = DemoFeed.candles(symbol)
                sig = self.engine.evaluate(symbol, "crypto", bars,
                                           index_change=0.6,
                                           time_label=TseData.tehran_now_label(),
                                           day_label=time.strftime("%Y-%m-%d"))
                results["crypto"].append(sig)
            for symbol in self.settings["tse_symbols"][:2]:
                bars = DemoFeed.candles(symbol)
                sig = self.engine.evaluate(symbol, "tse", bars,
                                           daily_candles=None, index_change=0.4,
                                           time_label=TseData.tehran_now_label(),
                                           day_label=time.strftime("%Y-%m-%d"))
                results["tse"].append(sig)
        else:
            self.state["demo"] = False

        # ---------- پیپرتریدینگ ----------
        for market in ("crypto", "tse"):
            for sig in results[market]:
                if not getattr(sig, "is_error", False) and not sig.rejected:
                    try:
                        self._paper_trade(market, sig)
                    except Exception:
                        pass

        # ---------- ذخیره ----------
        with self.lock:
            for market in ("tse", "crypto"):
                fresh = [s.to_dict() for s in results[market] if not getattr(s, "is_error", False)]
                old = self.state["signals"].get(market, [])
                merged = {}
                for s in old:
                    merged[s["symbol"]] = s
                for s in fresh:
                    merged[s["symbol"]] = s
                self.state["signals"][market] = list(merged.values())[-40:]
            self.last_scan = time.time()
            self.scan_count += 1
            self.save_state()
            for b in self.brokers.values():
                b.save_trades_csv()
        return results

    # ------------------------------------------------------
    def _error_signal(self, symbol, market, msg):
        from core.signal_engine import ValidatedSignal
        s = ValidatedSignal(symbol=symbol, market=market, rejected=True,
                            reject_reason=msg, time=TseData.tehran_now_label())
        s.is_error = True
        return s

    def _closed_market_signal(self, symbol):
        from core.signal_engine import ValidatedSignal
        s = ValidatedSignal(symbol=symbol, market="tse", time=TseData.tehran_now_label(),
                            session_day=TseData.tehran_now_label()[:10])
        s.rejected = True
        s.reject_reason = "بازار سهام تهران بسته است (۹:۰۰ تا ۱۲:۴۵، شنبه تا چهارشنبه)"
        return s

    # ------------------------------------------------------
    def market_snapshot(self):
        """قیمت‌های لحظه‌ای برای تب «بازار»"""
        out = {"crypto": [], "tse": [], "demo": bool(self.state.get("demo")),
               "tse_open": TseData.is_market_open(),
               "time": TseData.tehran_now_label()}
        crypto_ok = self.reachable["crypto"] is not False
        for symbol in self.settings["crypto_symbols"]:
            if crypto_ok:
                try:
                    p = NobitexData.get_prices([symbol])[symbol]
                    out["crypto"].append({"symbol": symbol, **p, "source": "نوبیتکس"})
                    continue
                except DataError:
                    pass
            if self.demo:
                p = DemoFeed.price(symbol)
                out["crypto"].append({"symbol": symbol, **p, "source": "نمایشی"})
            else:
                out["crypto"].append({"symbol": symbol, "error": "دریافت ناموفق", "source": "ناموفق"})
        for symbol in self.settings["tse_symbols"]:
            # حتی وقتی بازار بسته است، آخرین قیمت‌ها را می‌گیریم (اگر TSETMC در دسترس باشد)
            if self.reachable["tse"] is not False:
                try:
                    code = TseData.resolve_symbol(symbol)
                    rt = TseData.get_realtime(code)
                    self.reachable["tse"] = True
                    out["tse"].append({
                        "symbol": symbol, "last": rt["last"], "dayHigh": rt["high"],
                        "dayLow": rt["low"], "dayOpen": rt["open"],
                        "dayChange": (rt["last"] / rt["yclose"] - 1) * 100 if rt["yclose"] else 0,
                        "volume": rt["volume"], "source": "TSETMC"})
                    continue
                except DataError:
                    self.reachable["tse"] = False
            if self.demo:
                p = DemoFeed.price(symbol)
                out["tse"].append({"symbol": symbol, **p, "source": "نمایشی"})
            else:
                out["tse"].append({"symbol": symbol, "error": "دریافت ناموفق (IP ایران لازم است)", "source": "ناموفق"})
        return out

    def trades_report(self):
        """گزارش پیپرتریدینگ برای اپ"""
        out = {}
        for market, broker in self.brokers.items():
            closed = [t for t in broker.trades if t["side"] == "فروش"]
            pnls = [t["pnl"] for t in closed]
            last_price = None
            for s in self.state["signals"].get(market, []):
                if s.get("entry"):
                    last_price = s["entry"]
                    break
            out[market] = {
                "capital": broker.initial_capital,
                "cash": round(broker.cash, 0),
                "position_qty": broker.position_qty,
                "position_value": round(broker.position_qty * (last_price or 0), 0),
                "equity": round(broker.total_equity(last_price or 0), 0),
                "trades": broker.trades[-20:],
                "closed_count": len(closed),
                "wins": len([p for p in pnls if p > 0]),
                "total_pnl": round(sum(pnls), 0) if pnls else 0,
                "risk": {
                    "trades_today": self.risks[market].state.trades_today,
                    "max_trades": self.risks[market].max_trades_per_day,
                    "pnl_today": round(self.risks[market].state.realized_pnl_today, 0),
                    "halted": self.risks[market].state.halted,
                },
            }
        out["tse_open"] = TseData.is_market_open()
        out["demo"] = bool(self.state.get("demo"))
        return out

    def health(self):
        return {
            "status": "ok",
            "version": VERSION,
            "demo": bool(self.state.get("demo")),
            "tse_reachable": self.reachable["tse"],
            "crypto_reachable": self.reachable["crypto"],
            "tse_market_open": TseData.is_market_open(),
            "tehran_time": TseData.tehran_now_label(),
            "last_scan": TseData.tehran_now_label(self.last_scan) if self.last_scan else "هنوز اسکن نشده",
            "scan_count": self.scan_count,
            "uptime_sec": int(time.time() - self.started),
            "settings": self.settings,
        }

    # ------------------------------------------------------
    def loop(self):
        """حلقه پس‌زمینه"""
        time.sleep(1.5)      # اولین اسکن سریع — تا اپ فوراً دیتا ببیند
        while True:
            try:
                self.scan_once()
            except Exception as e:
                try:
                    self._append_note(f"خطای اسکن: {e.__class__.__name__}: {e}")
                except Exception:
                    pass
            interval = self.settings.get("scan_interval_sec", 90)
            if self.settings.get("low_data"):
                interval = max(interval, 90)
            time.sleep(interval)


SCANNER = None

def get_scanner():
    global SCANNER
    if SCANNER is None:
        SCANNER = Scanner()
        threading.Thread(target=SCANNER.loop, daemon=True).start()
    return SCANNER


# ============================================================
#  هندلر HTTP
# ============================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "SarkhatBot/" + VERSION

    # ---------- CORS (تا اپ بتواند از هر جایی وصل شود) ----------
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        pass     # لاگ خفیف — روی گوشی فایل لاگ ننویسیم

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        full = os.path.normpath(os.path.join(WEBAPP_DIR, path.lstrip("/")))
        if not full.startswith(WEBAPP_DIR) or not os.path.isfile(full):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "7")
            self._cors()
            self.end_headers()
            self.wfile.write("پیدا نشد".encode("utf-8"))
            return
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    # ---------- مسیرها ----------
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        sc = get_scanner()
        path, _, _query = self.path.partition("?")
        try:
            if path == "/api/health":
                self._json(sc.health())
            elif path == "/api/signals":
                with sc.lock:
                    payload = {"signals": sc.state.get("signals", {}),
                               "demo": bool(sc.state.get("demo")),
                               "last_scan": sc.health()["last_scan"],
                               "tse_open": TseData.is_market_open(),
                               "time": TseData.tehran_now_label()}
                self._json(payload)
            elif path == "/api/market":
                self._json(sc.market_snapshot())
            elif path == "/api/trades":
                self._json(sc.trades_report())
            elif path == "/api/settings":
                self._json(sc.settings)
            elif path == "/api/scan":
                results = sc.scan_once()
                self._json({"ok": True,
                            "signals": {m: [s.to_dict() for s in results[m]] for m in results}})
            elif path.startswith("/api/"):
                self._json({"error": "مسیر API پیدا نشد"}, 404)
            else:
                self._static(path)
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._json({"error": f"{e.__class__.__name__}: {e}"}, 500)
            except Exception:
                pass

    def do_POST(self):
        sc = get_scanner()
        path, _, _ = self.path.partition("?")
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            if path == "/api/settings":
                allowed = {"scan_interval_sec", "min_grade", "auto_paper",
                           "tse_symbols", "crypto_symbols", "low_data"}
                clean = {k: v for k, v in payload.items() if k in allowed}
                sc.save_settings(clean)
                self._json({"ok": True, "settings": sc.settings})
            elif path == "/api/scan":
                results = sc.scan_once()
                self._json({"ok": True,
                            "signals": {m: [s.to_dict() for s in results[m]] for m in results}})
            else:
                self._json({"error": "مسیر POST پیدا نشد"}, 404)
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "بدنه JSON نامعتبر بود"}, 400)
        except Exception as e:
            self._json({"error": f"{e.__class__.__name__}: {e}"}, 500)


# ============================================================
def run(port=8000):
    sc = get_scanner()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("=" * 58)
    print("  🤖 ربات سرخط‌زن — سرور و اپلیکیشن موبایل")
    print(f"  نسخه {VERSION} | پورت {port}")
    print(f"  ⏰ وقت تهران: {TseData.tehran_now_label()}")
    print(f"  📱 اپلیکیشن:      http://localhost:{port}/")
    print(f"  📡 وضعیت سرور:    http://localhost:{port}/api/health")
    print("  توقف: Ctrl+C")
    print("=" * 58)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n⏹ سرور متوقف شد")
        for b in sc.brokers.values():
            b.save_trades_csv()


if __name__ == "__main__":
    _port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000
    run(_port)
