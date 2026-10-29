# -*- coding: utf-8 -*-
"""
موتور سیگنال با درجه اعتبارسنجی
================================
هر سیگنال قبل از نمایش، از ۸ فیلتر مستقل عبور می‌کند و امتیاز ۰ تا ۱۰۰ می‌گیرد:

  ۱) شکست واقعی محدوده ابتدای جلسه           (حداکثر ۲۰ امتیاز)
  ۲) تأیید حجم — حجم شکست در برابر میانگین    (حداکثر ۲۰ امتیاز)
  ۳) روند روزانه (بالاتر از میانگین‌ها)        (حداکثر ۱۵ امتیاز)
  ۴) وضعیت RSI — نه اشباع خرید               (حداکثر ۱۰ امتیاز)
  ۵) کیفیت کندل شکست (بسته‌شدن نزدیک سقف)      (حداکثر ۱۰ امتیاز)
  ۶) نقدشوندگی و ارزش معاملات روزانه          (حداکثر ۱۰ امتیاز)
  ۷) هم‌جهتی با بازار کلی (شاخص/بیت‌کوین)      (حداکثر ۱۰ امتیاز)
  ۸) نسبت سود به ریسک                        (حداکثر  ۵ امتیاز)

درجه‌بندی:
  A ≥ ۸۰  →  اعتبار بالا   (سبز)
  B ≥ ۶۵  →  اعتبار متوسط  (زرد)
  C ≥ ۵۰  →  اعتبار کم     (نارنجی)
  زیر ۵۰  →  رد شده — اصلاً سیگنال حساب نمی‌آید

علاوه بر امتیاز، چند «فیلتر سخت» هم هست؛ اگر رد شوند حتی با امتیاز بالا
سیگنال معتبر نمی‌شود (مثلاً حجم خیلی کم، RSI خیلی بالا، اسپرد خیلی باز).
"""
from dataclasses import dataclass, field, asdict


# پارامترهای امتیازدهی — قابل تنظیم از config
SCORE_MIN_A = 80
SCORE_MIN_B = 65
SCORE_MIN_C = 50

HARD_MAX_RSI = 80          # بالای این RSI: رد قطعی (اشباع خرید)
HARD_MIN_VOL_RATIO = 1.3   # حجم کمتر از ۱.۳ برابر میانگین: رد قطعی
HARD_MAX_SPREAD = 0.009    # اسپرد بیشتر از ۰.۹٪: رد قطعی (نقدشوندگی بد)


@dataclass
class Factor:
    """یک فاکتور اعتبارسنجی برای نمایش در چک‌لیست"""
    key: str
    title: str
    passed: bool
    points: float
    max_points: float
    detail: str


@dataclass
class ValidatedSignal:
    """سیگنال کامل همراه با مدارک اعتبارسنجی"""
    symbol: str
    market: str                 # tse | crypto
    action: str = "hold"        # buy | hold
    grade: str = "-"            # A | B | C | -
    score: float = 0.0
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    rr: float = 0.0
    breakout_pct: float = 0.0
    volume_ratio: float = 0.0
    rsi: float = 50.0
    rejected: bool = False
    reject_reason: str = ""
    factors: list = field(default_factory=list)
    time: str = ""
    session_day: str = ""

    def to_dict(self):
        d = asdict(self)
        return d

    @property
    def is_valid(self):
        return (self.action == "buy") and not self.rejected and self.score >= SCORE_MIN_C


# ============================================================
#  محاسبات فنی (پایتون خالص — بدون pandas)
# ============================================================
def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(closes, period=14):
    """RSI وایلدر — خروجی بین ۰ تا ۱۰۰"""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ============================================================
#  موتور اصلی
# ============================================================
class SignalEngine:
    def __init__(self, range_bars=4, volume_mult=2.2, break_buffer=0.001,
                 stop_loss_pct=0.015, take_profit_rr=2.5):
        self.range_bars = range_bars
        self.volume_mult = volume_mult
        self.break_buffer = break_buffer
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_rr = take_profit_rr

    # ------------------------------------------------------
    def evaluate(self, symbol, market, session_candles,
                 daily_candles=None, index_change=0.0, spread_pct=None,
                 time_label="", day_label=""):
        """
        session_candles : کندل‌های ۱۵ دقیقه‌ای «امروز» به ترتیب زمانی (حداقل range_bars+1)
        daily_candles   : کندل‌های روزانه (اختیاری — برای فاکتور روند و نقدشوندگی)
        index_change    : درصد تغییر شاخص کل بورس یا بیت‌کوین امروز
        spread_pct      : اسپرد (برای کریپتو از عمق بازار؛ برای بورس None)
        """
        sig = ValidatedSignal(symbol=symbol, market=market, time=time_label,
                              session_day=day_label)
        bars = session_candles
        if len(bars) <= self.range_bars:
            sig.reject_reason = "کندل‌های جلسه هنوز کافی نیست"
            sig.rejected = True
            return sig

        # ---- محدوده ابتدای جلسه ----
        or_bars = bars[:self.range_bars]
        range_high = max(b["high"] for b in or_bars)
        range_low = min(b["low"] for b in or_bars)

        # میانگین حجم پیش از کندل جاری (خط پایه)
        j = len(bars) - 1
        bar = bars[j]
        base_start = max(0, j - 20)
        base_bars = bars[base_start:j]
        baseline_vol = (sum(b["volume"] for b in base_bars) / len(base_bars)) if base_bars else 0.0

        entry = float(bar["close"])
        vol_ratio = (float(bar["volume"]) / baseline_vol) if baseline_vol > 0 else 0.0
        breakout_gap = (entry - range_high) / range_high if range_high > 0 else 0.0
        sig.entry = entry
        sig.volume_ratio = round(vol_ratio, 2)
        sig.breakout_pct = round(breakout_gap * 100, 2)

        # ================= فیلترهای سخت =================
        # (۱) باید شکست رخ داده باشد
        trigger = range_high * (1 + self.break_buffer)
        if entry <= trigger:
            sig.reject_reason = "هنوز شکستی رخ نداده (قیمت زیر سقف محدوده)"
            sig.rejected = True
            sig.factors.append(Factor("breakout", "شکست محدوده اولیه", False, 0, 20,
                f"قیمت {entry:,.0f} زیر سطح شکست {trigger:,.0f}").__dict__)
            return self._finalize(sig)
        # (۲) حجم خیلی کم
        if vol_ratio < HARD_MIN_VOL_RATIO:
            sig.reject_reason = f"حجم شکست خیلی کم است ({vol_ratio:.1f} برابر میانگین)"
            sig.rejected = True
        # (۳) RSI خیلی بالا
        closes = [b["close"] for b in bars[-60:]]
        r = rsi(closes, 14)
        sig.rsi = round(r, 1)
        if r > HARD_MAX_RSI:
            sig.reject_reason = f"RSI = {r:.0f} — اشباع خرید، خطر اصلاح"
            sig.rejected = True
        # (۴) اسپرد خیلی باز
        if spread_pct is not None and spread_pct > HARD_MAX_SPREAD:
            sig.reject_reason = f"اسپرد {spread_pct*100:.1f}٪ خیلی باز است (نقدشوندگی کم)"
            sig.rejected = True

        # ================= امتیازدهی =================
        factors = []
        total = 0.0

        # ۱) شکست محدوده — ۲۰ امتیاز
        f = Factor("breakout", "شکست محدوده ابتدای جلسه", True, 0, 20, "")
        if breakout_gap >= 0.008:
            f.points = 20
        elif breakout_gap >= 0.004:
            f.points = 16
        elif breakout_gap >= 0.002:
            f.points = 12
        else:
            f.points = 8
        f.detail = f"شکست {sig.breakout_pct:+.2f}٪ بالای سقف {range_high:,.0f}"
        total += f.points
        factors.append(f)

        # ۲) تأیید حجم — ۲۰ امتیاز
        f = Factor("volume", "تأیید حجم معاملات", vol_ratio >= self.volume_mult, 0, 20, "")
        if vol_ratio >= 3.0:
            f.points = 20
        elif vol_ratio >= self.volume_mult:
            f.points = 17
        elif vol_ratio >= HARD_MIN_VOL_RATIO:
            f.points = 10
        f.detail = f"حجم کندل {sig.volume_ratio} برابر میانگین (حد لازم {self.volume_mult})"
        total += f.points
        factors.append(f)

        # ۳) روند روزانه — ۱۵ امتیاز
        f = Factor("trend", "روند روزانه صعودی", False, 0, 15, "دیتای روزانه موجود نیست")
        if daily_candles and len(daily_candles) >= 20:
            dcloses = [c["close"] for c in daily_candles]
            s20 = sma(dcloses, 20)
            s50 = sma(dcloses, 50) or s20
            above20 = entry > s20
            s20_rising = len(dcloses) > 5 and s20 > (sum(dcloses[-25:-5]) / 20)
            above50 = entry > s50
            pts = 0
            if above20:
                pts += 8
            if s20_rising:
                pts += 4
            if above50:
                pts += 3
            f.points = pts
            f.passed = pts >= 8
            f.detail = (f"قیمت {'بالاتر از' if above20 else 'زیرِ'} میانگین ۲۰ روزه "
                        f"({s20:,.0f}) | میانگین {'صعودی' if s20_rising else 'نزولی'}")
        total += f.points
        factors.append(f)

        # ۴) RSI — ۱۰ امتیاز
        f = Factor("rsi", "وضعیت RSI (نه اشباع خرید)", False, 0, 10, "")
        if 50 <= r <= 68:
            f.points = 10
        elif 68 < r <= 75:
            f.points = 6
        elif 45 <= r < 50:
            f.points = 4
        elif 75 < r <= HARD_MAX_RSI:
            f.points = 2
        f.passed = f.points >= 6
        f.detail = f"RSI(14) روی کندل ۱۵ دقیقه‌ای = {r:.0f}"
        total += f.points
        factors.append(f)

        # ۵) کیفیت کندل شکست — ۱۰ امتیاز (بسته‌شدن نزدیک سقف کندل)
        f = Factor("candle", "کیفیت کندل شکست", False, 0, 10, "")
        rng = bar["high"] - bar["low"]
        if rng > 0:
            close_pos = (bar["close"] - bar["low"]) / rng
            if close_pos >= 0.75:
                f.points = 10
            elif close_pos >= 0.5:
                f.points = 6
            else:
                f.points = 2
            f.detail = f"بسته‌شدن در {close_pos*100:.0f}٪ بالای کندل"
        else:
            f.detail = "کندل بدون دامنه"
        f.passed = f.points >= 6
        total += f.points
        factors.append(f)

        # ۶) نقدشوندگی — ۱۰ امتیاز
        f = Factor("liquidity", "نقدشوندگی کافی", False, 0, 10, "")
        if market == "tse" and daily_candles and len(daily_candles) >= 5:
            recent = daily_candles[-5:]
            avg_value = sum(c["close"] * c["volume"] for c in recent) / len(recent)
            # آستانه: ۵۰ میلیارد ریال ارزش روزانه (۵ میلیارد تومان)
            if avg_value >= 50e9:
                f.points = 10
            elif avg_value >= 10e9:
                f.points = 6
            elif avg_value >= 2e9:
                f.points = 3
            f.detail = f"میانگین ارزش روزانه: {avg_value/1e9:,.1f} میلیارد ریال"
        elif market == "crypto":
            recent = bars[-10:] if len(bars) >= 10 else bars
            avg_vol = sum(b["volume"] for b in recent) / len(recent)
            if avg_vol >= 5:      # حداقل ۵ واحد در هر کندل ۱۵ دقیقه‌ای
                f.points = 10
            elif avg_vol >= 1:
                f.points = 6
            else:
                f.points = 2
            f.detail = f"میانگین حجم هر کندل: {avg_vol:.2f} واحد"
        else:
            f.points = 5
            f.detail = "بدون دیتای روزانه — امتیاز خنثی"
        f.passed = f.points >= 6
        total += f.points
        factors.append(f)

        # ۷) هم‌جهتی بازار — ۱۰ امتیاز
        f = Factor("market", "هم‌جهتی با بازار کلی", False, 0, 10, "")
        if index_change > 0.3:
            f.points = 10
        elif index_change > 0:
            f.points = 6
        elif index_change > -0.3:
            f.points = 3
        f.detail = f"تغییر شاخص بازار امروز: {index_change:+.2f}٪"
        f.passed = f.points >= 6
        total += f.points
        factors.append(f)

        # ۸) نسبت سود به ریسک — ۵ امتیاز
        stop = min(range_low, entry * (1 - self.stop_loss_pct))
        target = entry + self.take_profit_rr * (entry - stop)
        rr = (target - entry) / max(entry - stop, 1e-9)
        sig.stop = stop
        sig.target = target
        sig.rr = round(rr, 2)
        f = Factor("rr", "نسبت سود به ریسک", rr >= 2.0, 0, 5,
                   f"ریسک {entry-stop:,.0f} در برابر سود {target-entry:,.0f} (R/R={rr:.1f})")
        f.points = 5 if rr >= self.take_profit_rr else (3 if rr >= 2 else 1)
        total += f.points
        factors.append(f)

        sig.factors = [f.__dict__ for f in factors]
        sig.score = round(total, 1)
        sig.action = "buy"

        # درجه‌بندی
        if sig.score >= SCORE_MIN_A:
            sig.grade = "A"
        elif sig.score >= SCORE_MIN_B:
            sig.grade = "B"
        elif sig.score >= SCORE_MIN_C:
            sig.grade = "C"
        else:
            sig.grade = "-"
            sig.rejected = True
            sig.reject_reason = f"امتیاز {sig.score} زیر حداقل اعتبار ({SCORE_MIN_C})"
        return self._finalize(sig)

    # ------------------------------------------------------
    def _finalize(self, sig):
        if sig.rejected and not sig.factors:
            sig.factors = [{
                "key": "hard", "title": "فیلتر سخت", "passed": False,
                "points": 0, "max_points": 0, "detail": sig.reject_reason,
            }]
        return sig


# ============================================================
#  تست سریع داخلی
# ============================================================
if __name__ == "__main__":
    import random
    eng = SignalEngine()
    # ساخت یک جلسه شبیه‌سازی‌شده: محدوده کم‌نوسان + شکست پرحجم
    random.seed(2)
    bars = []
    t0 = 1700000000
    for i in range(4):
        p = 100_000 + random.randint(-200, 200)
        bars.append({"time": t0 + i * 900, "open": p - 100, "high": p + 300,
                     "low": p - 300, "close": p, "volume": 1000})
    for i in range(4, 12):
        p = 100_000 + (i - 3) * 800
        bars.append({"time": t0 + i * 900, "open": p - 200, "high": p + 500,
                     "low": p - 400, "close": p + 400, "volume": 3500 if i >= 8 else 900})
    s = eng.evaluate("TEST", "crypto", bars, index_change=0.5)
    print(f"امتیاز: {s.score} | درجه: {s.grade} | رد: {s.rejected} | دلیل: {s.reject_reason}")
    for f in s.factors:
        mark = "OK " if f["passed"] else "-- "
        print(f"  [{mark}] {f['title']}: {f['points']}/{f['max_points']} — {f['detail']}")
