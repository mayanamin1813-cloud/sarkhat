# -*- coding: utf-8 -*-
"""
دریافت دیتای واقعی بازار — بورس تهران + نوبیتکس
=================================================
این ماژول با «فقط کتابخانه استاندارد پایتون» کار می‌کند (بدون pandas و requests)
تا روی گوشی اندروید با Termux هم بدون دردسر اجرا شود.

منابع دیتا (اندپوینت‌های عمومی و واقعی):
  بورس تهران:
    - تاریخچه روزانه:  cdn.tsetmc.com/Loader.aspx?ParTree=15131P&i=<کد نماد>
    - تاریخچه جایگزین: tsetmc.com/tsev2/data/Export-txt.aspx?t=i&a=1&b=0&i=<کد>
    - قیمت لحظه‌ای:    tsetmc.com/tsev2/data/instinf.aspx?i=<کد>
    - جستجوی نماد:     tsetmc.com/tsev2/data/search.aspx?skey=<نام>
  نوبیتکس:
    - کندل:            api.nobitex.ir/market/udf/history
    - قیمت لحظه‌ای:    api.nobitex.ir/market/stats
    - عمق بازار:       api.nobitex.ir/v2/orderbook/<نماد>

⚠️ سرورهای TSETMC فقط از IP ایران پاسخ می‌دهند. اگر خطای شبکه گرفتید،
   مشکل از کد نیست — ربات باید داخل ایران (گوشی/کامپیوتر/سرور ایران) اجرا شود.
"""
import json
import re
import time
import urllib.request
import urllib.parse
import urllib.error

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TIMEOUT = 12          # ثانیه — اینترنت ایران را حساب کرده‌ایم
RETRIES = 2

# تقویم و ساعت بازار سهام تهران (ایران از ۱۴۰۱ ساعت تابستانی ندارد → +۳:۳۰ ثابت)
TEHRAN_UTC_OFFSET = 3.5 * 3600      # ثانیه
SESSION_OPEN_SEC = 9 * 3600         # ۰۹:۰۰
SESSION_CLOSE_SEC = int(12.75 * 3600)  # ۱۲:۴۵ (شامل حراج پایانی)
TRADING_WEEKDAYS = (0, 1, 2, 5, 6)  # دوشنبه=0 ... یکشنبه=6 → شنبه،یکشنبه،دوشنبه،سه‌شنبه،چهارشنبه


class DataError(Exception):
    """خطای دریافت دیتا — پیام فارسی برای کاربر"""


# ============================================================
#  زیرساخت HTTP (سبک، با تلاش مجدد و کش)
# ============================================================
def http_get(url, timeout=TIMEOUT, retries=RETRIES, headers=None):
    """GET ساده با تلاش مجدد؛ خروجی: متن خام"""
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                **(headers or {}),
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError,
                OSError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))   # صبر کوتاه و تلاش دوباره
    raise DataError(f"اتصال به سرور ناموفق بود ({url.split('/')[2]}): {last_err}")


def http_post_form(url, fields, timeout=TIMEOUT, retries=RETRIES):
    """POST با فرم ساده (برای اندپوینت‌هایی که POST می‌خواهند)"""
    data = urllib.parse.urlencode(fields).encode("utf-8")
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError,
                OSError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise DataError(f"اتصال به سرور ناموفق بود ({url.split('/')[2]}): {last_err}")


class TTLCache:
    """کش ساده در حافظه — برای حالت کم‌مصرف تا هر بار دیتا نگیرد"""
    def __init__(self, ttl_seconds=60, max_items=200):
        self.ttl = ttl_seconds
        self.max_items = max_items
        self._store = {}

    def get(self, key):
        item = self._store.get(key)
        if item and time.time() - item[0] < self.ttl:
            return item[1]
        return None

    def set(self, key, value):
        if len(self._store) >= self.max_items:
            # قدیمی‌ترین‌ها را حذف کن
            oldest = sorted(self._store.items(), key=lambda kv: kv[1][0])[:20]
            for k, _ in oldest:
                self._store.pop(k, None)
        self._store[key] = (time.time(), value)


_cache = TTLCache(ttl_seconds=45)

# ------------------------------------------------------------
#  «مدار شکن»: اگر یک سرور در دسترس نبود، تا ۱۲۰ ثانیه به آن چنگ نزنیم
#  تا ربات روی اینترنت بدون دسترسی، کند نشود.
# ------------------------------------------------------------
_down_until = {"nobitex": 0.0, "tsetmc": 0.0}
DOWN_COOLDOWN = 120


def _mark_down(key):
    _down_until[key] = time.time() + DOWN_COOLDOWN


def _mark_up(key):
    _down_until[key] = 0.0


def _is_down(key):
    return time.time() < _down_until[key]


# ============================================================
#  کلاس پایه
# ============================================================
class Candle(dict):
    """کندل: dict با کلیدهای time,open,high,low,close,volume"""
    pass


def _num(x, default=0.0):
    """تبدیل امن به float — عدد فارسی/ممیز کاما/فاصله را هم می‌فهمد"""
    if x is None:
        return default
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", ".").replace("\u066b", ".")
    s = re.sub(r"[^0-9.\-eE]", "", s)
    try:
        return float(s)
    except ValueError:
        return default


def _validate_candles(candles):
    """کندل‌های خراب را دور می‌ریزد تا موتور سیگنال هرگز داده نامعتبر نبیند"""
    out = []
    for c in candles:
        try:
            o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
            v = float(c.get("volume", 0) or 0)
            if o <= 0 or cl <= 0 or h <= 0 or l <= 0:
                continue
            if h < max(o, cl) - 1e-9 or l > min(o, cl) + 1e-9:
                # ترمیم نرم: سقف/کف ناسازگار را با max/min اصلاح کن
                h = max(h, o, cl)
                l = min(l, o, cl)
            out.append({"time": c.get("time"), "open": o, "high": h,
                        "low": l, "close": cl, "volume": max(v, 0.0)})
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ============================================================
#  ۱) نوبیتکس — کریپتو (دقیق‌ترین دیتای ۱۵ دقیقه‌ای)
# ============================================================
class NobitexData:
    BASE = "https://api.nobitex.ir"

    @classmethod
    def get_candles(cls, symbol="BTCIRT", resolution=15, days=14):
        """
        کندل‌های واقعی نوبیتکس.
        resolution: 1,5,15,30,60 دقیقه یا D روزانه
        خروجی: لیست Candle به ترتیب زمانی
        """
        key = f"nb-candles-{symbol}-{resolution}-{days}"
        hit = _cache.get(key)
        if hit is not None:
            return hit
        if _is_down("nobitex"):
            raise DataError("نوبیتکس اکنون در دسترس نیست (تلاش دوباره خودکار انجام می‌شود)")

        to_ts = int(time.time())
        from_ts = to_ts - int(days * 86400)
        q = urllib.parse.urlencode({"symbol": symbol.upper(), "resolution": resolution,
                                    "from": from_ts, "to": to_ts})
        raw = None
        # مسیر اصلی و مسیر جایگزین — هرکدام جواب داد همان می‌ماند
        for path in (f"/market/udf/history?{q}", f"/v2/udf/history?{q}"):
            try:
                raw = http_get(cls.BASE + path, timeout=8, retries=1)
                data = json.loads(raw)
                if data.get("s") == "ok" and data.get("c"):
                    _mark_up("nobitex")
                    break
                raw = None
            except (DataError, ValueError):
                raw = None
        if raw is None:
            _mark_down("nobitex")
            raise DataError(
                f"کندل {symbol} از نوبیتکس دریافت نشد. "
                f"(اتصال اینترنت یا دسترسی به api.nobitex.ir را بررسی کن)")

        candles = []
        for i, ts in enumerate(data["t"]):
            candles.append(Candle(time=int(ts), open=_num(data["o"][i]),
                                  high=_num(data["h"][i]), low=_num(data["l"][i]),
                                  close=_num(data["c"][i]), volume=_num(data["v"][i])))
        candles = _validate_candles(candles)
        if not candles:
            raise DataError(f"دیتای {symbol} خالی یا خراب بود")
        _cache.set(key, candles)
        return candles

    @classmethod
    def get_prices(cls, pairs=("BTCIRT", "ETHIRT", "USDTIRT", "BNBITR")):
        """قیمت لحظه‌ای چند ارز — یک درخواست برای همه (کم‌مصرف)"""
        pairs = [p.upper() for p in pairs]
        key = "nb-prices-" + ",".join(pairs)
        hit = _cache.get(key)
        if hit is not None:
            return hit

        src = ",".join(p.replace("IRT", "").replace("ITR", "") for p in pairs)
        src = src.replace("BNB", "BNB").replace("USDT", "USDT").replace("ETH", "ETH").replace("BTC", "BTC")
        body = {"srcCurrency": src.lower(), "dstCurrency": "irt"}
        if _is_down("nobitex"):
            raise DataError("نوبیتکس اکنون در دسترس نیست")
        try:
            raw = http_post_form(cls.BASE + "/market/stats", body, timeout=8, retries=1)
            data = json.loads(raw)
            _mark_up("nobitex")
        except (DataError, ValueError) as e:
            _mark_down("nobitex")
            raise DataError(f"قیمت‌های نوبیتکس دریافت نشد: {e}")

        out = {}
        stats = data.get("stats") or {}
        for p in pairs:
            k = p.lower().replace("itr", "irt")
            # نوبیتکس جفت‌ها را بدون پسوند IRT ذخیره می‌کند: btcirt یا btcitr
            s = stats.get(k) or stats.get(k.replace("irt", "itr")) or stats.get(p.lower())
            if s:
                out[p] = {
                    "last": _num(s.get("latest")),
                    "dayHigh": _num(s.get("dayHigh")),
                    "dayLow": _num(s.get("dayLow")),
                    "dayOpen": _num(s.get("dayOpen")),
                    "dayChange": _num(s.get("dayChange")),
                    "bestBuy": _num(s.get("bestBuy")),
                    "bestSell": _num(s.get("bestSell")),
                    "isClosed": bool(s.get("isClosed")),
                }
        if not out:
            raise DataError("پاسخ قیمت نوبیتکس خالی بود")
        _cache.set(key, out)
        return out

    @classmethod
    def get_orderbook(cls, symbol="BTCIRT"):
        """عمق بازار — برای سنجش اسپرد و نقدشوندگی قبل از سیگنال"""
        key = f"nb-ob-{symbol}"
        hit = _cache.get(key)
        if hit is not None:
            return hit
        if _is_down("nobitex"):
            raise DataError("نوبیتکس اکنون در دسترس نیست")
        try:
            raw = http_get(f"{cls.BASE}/v2/orderbook/{symbol.upper()}", timeout=8, retries=1)
            data = json.loads(raw)
            _mark_up("nobitex")
        except (DataError, ValueError) as e:
            _mark_down("nobitex")
            raise DataError(f"عمق بازار {symbol} دریافت نشد: {e}")
        asks = [[_num(p), _num(a)] for p, a in (data.get("asks") or [])[:10]]
        bids = [[_num(p), _num(a)] for p, a in (data.get("bids") or [])[:10]]
        out = {"asks": asks, "bids": bids}
        _cache.set(key, out)
        return out


# ============================================================
#  ۲) بورس تهران (TSETMC)
# ============================================================
class TseData:
    """دیتای واقعی بورس تهران — کد نماد (insCode) خودکار پیدا و کش می‌شود"""

    SEARCH_URL = "http://www.tsetmc.com/tsev2/data/search.aspx?skey="
    REALTIME_URL = "http://www.tsetmc.com/tsev2/data/instinf.aspx?i="
    HISTORY_JSON_URL = "https://cdn.tsetmc.com/Loader.aspx?ParTree=15131P&i="
    HISTORY_TXT_URL = "http://www.tsetmc.com/tsev2/data/Export-txt.aspx?t=i&a=1&b=0&i="

    # کد نمادهای رایج (اگر درست نبود، ربات خودش با جستجو کش می‌سازد)
    BUILTIN_CODES = {}

    _codes_file = None      # کش پایدار روی دیسک — در init_state ست می‌شود
    _scale_cache = {}       # ضریب مقیاس قیمت هر نماد، خودکار کالیبره می‌شود

    # ------------------------------------------------------
    @classmethod
    def _load_code_cache(cls):
        if cls._codes_file is None:
            return {}
        try:
            with open(cls._codes_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    @classmethod
    def _save_code_cache(cls, codes):
        if cls._codes_file is None:
            return
        try:
            with open(cls._codes_file, "w", encoding="utf-8") as f:
                json.dump(codes, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    @classmethod
    def resolve_symbol(cls, name):
        """نام فارسی نماد → کد عددی TSETMC (با کش پایدار)"""
        name = name.strip()
        if name.isdigit():
            return name
        codes = cls._load_code_cache()
        if name in codes:
            return codes[name]
        if name in cls.BUILTIN_CODES:
            return cls.BUILTIN_CODES[name]
        if _is_down("tsetmc"):
            raise DataError("TSETMC اکنون در دسترس نیست (تلاش دوباره خودکار انجام می‌شود)")

        # جستجوی آنلاین
        q = urllib.parse.quote(name)
        try:
            raw = http_get(cls.SEARCH_URL + q, timeout=8, retries=1)
            _mark_up("tsetmc")
        except DataError as e:
            _mark_down("tsetmc")
            raise DataError(f"جستجوی نماد «{name}» ناموفق: {e}")
        rows = [r for r in raw.split(";") if r.strip()]
        for row in rows:
            f = row.split(",")
            if len(f) >= 4 and f[0].strip() == name:
                code = f[1].strip()
                if code.isdigit():
                    codes[name] = code
                    cls._save_code_cache(codes)
                    return code
        # تطبیق نرم: اگر نام دقیق نبود، اولین نتیجه‌ای که شامل نام باشد
        for row in rows:
            f = row.split(",")
            if len(f) >= 4 and name in f[0]:
                code = f[1].strip()
                if code.isdigit():
                    codes[name] = code
                    cls._save_code_cache(codes)
                    return code
        raise DataError(f"نماد «{name}» در TSETMC پیدا نشد")

    # ------------------------------------------------------
    @classmethod
    def get_realtime(cls, ins_code):
        """
        قیمت لحظه‌ای نماد از instinf.
        خروجی: dict با کلیدهای last, close, open, high, low, yclose, volume, count, date, time
        """
        key = f"tse-rt-{ins_code}"
        hit = _cache.get(key)
        if hit is not None:
            return hit
        if _is_down("tsetmc"):
            raise DataError("TSETMC اکنون در دسترس نیست")

        try:
            raw = http_get(cls.REALTIME_URL + str(ins_code), timeout=8, retries=1)
            _mark_up("tsetmc")
        except DataError:
            _mark_down("tsetmc")
            raise
        first_section = raw.split(";")[0]
        f = first_section.split(",")
        if len(f) < 15:
            raise DataError(f"پاسخ لحظه‌ای TSETMC غیرمنتظره بود (طول {len(f)})")
        info = {
            "insCode": f[0].strip(),
            "date": f[3].strip(),
            "time": f[4].strip(),
            "open": _num(f[5]),
            "yclose": _num(f[6]),
            "last": _num(f[7]),
            "close": _num(f[8]),
            "count": _num(f[9]),
            "volume": _num(f[10]),
            "value": _num(f[11]),
            "low": _num(f[12]),
            "high": _num(f[13]),
        }
        # اعتبارسنجی منطقی — اگر ساختار عوض شده باشد اینجا می‌فهمیم
        if info["close"] <= 0 or info["last"] <= 0:
            raise DataError("قیمت لحظه‌ای معتبر نبود (ساختار پاسخ TSETMC تغییر کرده؟)")
        if info["high"] and info["low"] and info["high"] < info["low"]:
            info["high"], info["low"] = info["low"], info["high"]
        _cache.set(key, info)
        return info

    # ------------------------------------------------------
    @classmethod
    def _parse_history_json(cls, raw):
        """پارس JSON تاریخی Loader.aspx — با دو چیدمان شناخته‌شده ستون‌ها"""
        data = json.loads(raw)
        if not isinstance(data, list) or not data:
            return []
        ncols = len(data[0])
        candles = []
        for row in data:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            date = str(row[0]).strip()
            if not re.match(r"^\d{8}$", date):
                continue
            try:
                if ncols >= 8:
                    # چیدمان رایج: [date, open, high, low, yclose, close, volume, count]
                    o, h, l, y, c = (_num(row[1]), _num(row[2]), _num(row[3]),
                                     _num(row[4]), _num(row[5]))
                    v, n = _num(row[6]), _num(row[7])
                else:
                    # چیدمان کوتاه: [date, open, high, low, close, volume, count]
                    o, h, l, y, c = (_num(row[1]), _num(row[2]), _num(row[3]),
                                     _num(row[3]), _num(row[4]))
                    v, n = _num(row[5]), _num(row[6])
            except (IndexError, TypeError, ValueError):
                continue
            # اعتبارسنجی ساختاری — اگر ترتیب ستون‌ها متفاوت باشد رد می‌شود
            if o <= 0 or c <= 0 or h <= 0 or l <= 0 or h < max(o, c) * 1.2 or l < min(o, c) * 0.2:
                continue
            candles.append(Candle(time=date, open=o, high=h, low=l, close=c, volume=v))
        return candles

    @classmethod
    def _parse_history_txt(cls, raw):
        """پارس CSV تاریخی Export-txt (۱۲ ستون؛ ممیز با کاما)"""
        candles = []
        for line in raw.strip().splitlines():
            f = line.strip().split(",")
            if len(f) < 9:
                continue
            date = f[0].strip()
            if not re.match(r"^\d{8}$", date):
                continue
            try:
                o, h, l = _num(f[2]), _num(f[3]), _num(f[4])
                c = _num(f[6])
                v = _num(f[7])
            except (IndexError, ValueError):
                continue
            if o <= 0 or c <= 0:
                continue
            h = max(h, o, c)
            l = min(l, o, c) if l > 0 else min(o, c)
            candles.append(Candle(time=date, open=o, high=h, low=l, close=c, volume=max(v, 0)))
        return candles

    @classmethod
    def _calibrate_scale(cls, ins_code, candles):
        """
        مقیاس قیمت تاریخچه را با قیمت لحظه‌ای تنظیم می‌کند.
        برخی اندپوینت‌ها قیمت را در ریال با ضریب ×۱۰/×۱۰۰/... می‌دهند؛
        نسبت قیمت لحظه‌ای به آخرین قیمت تاریخچه باید ۱ (یا نزدیک آن) باشد.
        """
        try:
            rt = cls.get_realtime(ins_code)
        except DataError:
            return candles    # لحظه‌ای نبود — دست نمی‌زنیم
        if not candles:
            return candles
        last_daily = candles[-1]["close"]
        if last_daily <= 0:
            return candles
        ratio = rt["last"] / last_daily
        # فقط اگر نسبت دقیقاً یکی از توان‌های ۱۰ بود اصلاح کن
        for power in (10000, 1000, 100, 10, 0.1, 0.01):
            if abs(ratio - power) / max(power, 1e-9) < 0.12:
                if power != 1:
                    for c in candles:
                        c["open"] /= power
                        c["high"] /= power
                        c["low"] /= power
                        c["close"] /= power
                cls._scale_cache[ins_code] = 1.0 / power
                break
        return candles

    @classmethod
    def get_daily_history(cls, symbol, days=180):
        """
        دیتای روزانه واقعی نماد (برای روند، میانگین حجم، فیلتر نقدشوندگی).
        اول JSON رسمی، اگر نشد CSV جایگزین.
        خروجی به «تومان» نرمال می‌شود (قیمت لحظه‌ای TSETMC ریال است).
        """
        ins_code = cls.resolve_symbol(symbol)
        key = f"tse-hist-{ins_code}-{days}"
        hit = _cache.get(key)
        if hit is not None:
            return hit
        if _is_down("tsetmc"):
            raise DataError("TSETMC اکنون در دسترس نیست")

        candles = []
        try:
            raw = http_get(cls.HISTORY_JSON_URL + str(ins_code), timeout=15, retries=0)
            candles = cls._parse_history_json(raw)
            _mark_up("tsetmc")
        except (DataError, ValueError):
            candles = []
        if len(candles) < 10:
            try:
                raw = http_get(cls.HISTORY_TXT_URL + str(ins_code), timeout=15, retries=0)
                candles = cls._parse_history_txt(raw)
                _mark_up("tsetmc")
            except (DataError, ValueError):
                candles = []
        if len(candles) < 10:
            _mark_down("tsetmc")
            raise DataError(
                f"تاریخچه {symbol} دریافت نشد (احتمالاً اتصال به TSETMC ممکن نیست)")

        candles.sort(key=lambda c: str(c["time"]))
        candles = candles[-days:]
        candles = cls._calibrate_scale(ins_code, candles)
        _cache.set(key, candles)
        return candles

    # ------------------------------------------------------
    @classmethod
    def is_market_open(cls, utc_ts=None):
        """آیا بازار سهام تهران باز است؟ (شنبه تا چهارشنبه ۹ تا ۱۲:۴۵ به وقت تهران)"""
        utc_ts = utc_ts or time.time()
        local = time.gmtime(utc_ts + TEHRAN_UTC_OFFSET)
        if local.tm_wday not in TRADING_WEEKDAYS:
            return False
        sec = local.tm_hour * 3600 + local.tm_min * 60 + local.tm_sec
        return SESSION_OPEN_SEC <= sec <= SESSION_CLOSE_SEC

    @classmethod
    def tehran_now_label(cls, utc_ts=None):
        utc_ts = utc_ts or time.time()
        local = time.gmtime(utc_ts + TEHRAN_UTC_OFFSET)
        return time.strftime("%Y-%m-%d %H:%M", local)


# ============================================================
#  ابزار مشترک برای اسکنر
# ============================================================
def get_crypto_session(symbol="BTCIRT", days=3):
    """کندل‌های ۱۵ دقیقه‌ای واقعی کریپتو (به اندازه چند روز برای محدوده و میانگین‌ها)"""
    return NobitexData.get_candles(symbol=symbol, resolution=15, days=days)


def get_tse_session_snapshot(symbol):
    """یک اسنپ‌شات لحظه‌ای نماد بورس (برای ساخت کندل ۱۵ دقیقه‌ای در حافظه)"""
    code = TseData.resolve_symbol(symbol)
    return TseData.get_realtime(code)


if __name__ == "__main__":
    # تست سریع از داخل ایران:
    #   python core/market_data.py
    print("🔍 تست اتصال نوبیتکس ...")
    try:
        p = NobitexData.get_prices(["BTCIRT", "USDTIRT"])
        for k, v in p.items():
            print(f"  {k}: {v['last']:,.0f} تومان | تغییر روز: {v['dayChange']:.2f}٪")
        c = NobitexData.get_candles("BTCIRT", 15, 2)
        print(f"  کندل‌های BTCIRT: {len(c)} عدد | آخرین بسته‌شدن: {c[-1]['close']:,.0f}")
    except DataError as e:
        print(f"  ⚠️ {e}")
    print("🔍 تست اتصال TSETMC ...")
    try:
        code = TseData.resolve_symbol("فولاد")
        print(f"  کد فولاد: {code}")
        rt = TseData.get_realtime(code)
        print(f"  آخرین قیمت: {rt['last']:,.0f} ریال | حجم: {rt['volume']:,.0f}")
        h = TseData.get_daily_history("فولاد", 30)
        print(f"  تاریخچه روزانه: {len(h)} ردیف | آخرین بستن: {h[-1]['close']:,.0f}")
    except DataError as e:
        print(f"  ⚠️ {e}")
