# -*- coding: utf-8 -*-
"""
تولید داده نمونه (شبیه‌سازی‌شده) برای بک‌تست
===============================================
وقتی به دیتای واقعی دسترسی نداریم (مثلاً تست اولیه یا سرور خارج از ایران)،
این ماژول داده‌ای «واقع‌گرایانه» تولید می‌کند:
  - روند روزانه متغیر (روزهای رونددار و روزهای رنج)
  - نوسان بیشتر در ابتدای جلسه (مثل بازار واقعی)
  - حجم با الگوی U شکل
اعداد با seed ثابت تولید می‌شوند تا نتیجه تکرارپذیر باشد.
"""
import numpy as np
import pandas as pd


def generate_sample_15m(days=90, start_price=100_000.0, seed=42,
                        bars_per_day=26, market="tse"):
    """
    تولید کندل‌های ۱۵ دقیقه‌ای شبیه‌سازی‌شده.
      tse   : روزی ۲۶ کندل (۹ تا ۱۵:۳۰) با گپ افتتاح
      crypto: روزی ۹۶ کندل (۲۴ ساعته) بدون گپ
    """
    rng = np.random.default_rng(seed)
    all_rows = []
    price = start_price
    day0 = pd.Timestamp("2025-06-01")

    for d in range(days):
        if market == "tse":
            open_hour = 9
            n_bars = bars_per_day
            gap = rng.normal(0, 0.01)                       # گپ افتتاح ±۱٪
            daily_drift = rng.normal(0, 0.008)              # روند روز
            is_trend_day = rng.random() < 0.35              # ۳۵٪ روزها رونددار
        else:
            open_hour = 0
            n_bars = 96
            gap = 0.0
            daily_drift = rng.normal(0, 0.006)
            is_trend_day = rng.random() < 0.45

        # پدیده مومنتوم: در برخی روزها بعد از شکستِ سقف، حرکت ادامه پیدا می‌کند
        # (همان چیزی که استراتژی شکست محدوده هدف می‌گیرد)
        is_breakout_day = rng.random() < (0.35 if market == "tse" else 0.40)
        breakout_bar = int(rng.integers(5, 13))
        breakout_dir = 1 if rng.random() < 0.68 else -1
        breakout_strength = rng.uniform(0.02, 0.06)
        breakout_len = int(rng.integers(10, 20))
        breakout_extra = (breakout_dir * breakout_strength / breakout_len) if is_breakout_day else 0.0

        price = price * (1 + gap)
        # روند‌دار بودن روز: حرکت روزانه در طول کندل‌های روز پخش می‌شود
        trend_per_bar = (daily_drift * 2.5 / n_bars) if is_trend_day else 0.0
        base_vol = price * rng.uniform(0.004, 0.009) if market == "tse" \
            else price * rng.uniform(0.003, 0.006)

        for b in range(n_bars):
            minutes = open_hour * 60 + b * 15
            ts = day0 + pd.Timedelta(days=d) + pd.Timedelta(minutes=minutes)

            # نوسان فصلی: ابتدای جلسه پرنوسان‌تر
            season = 1.6 if (market == "tse" and b < 6) else 1.0
            drift = trend_per_bar
            if is_breakout_day and breakout_bar <= b < breakout_bar + breakout_len:
                drift += breakout_extra
            shock = rng.normal(drift, base_vol / price * season)

            o = price
            c = o * (1 + shock)
            h = max(o, c) * (1 + abs(rng.normal(0, 0.0015)))
            l = min(o, c) * (1 - abs(rng.normal(0, 0.0015)))

            # حجم: الگوی U + انفجار حجم در فاز شکست (مثل بازار واقعی)
            pos = b / max(n_bars - 1, 1)
            u_shape = 1.0 + 0.8 * (pos < 0.15) + 0.6 * (pos > 0.85)
            in_breakout = is_breakout_day and breakout_bar <= b < breakout_bar + breakout_len
            vol = rng.uniform(800, 1200) * u_shape * (1 + abs(shock) * 80)
            if in_breakout:
                vol *= rng.uniform(1.8, 3.2)   # کندل‌های شکست در بازار واقعی ۲ تا ۴ برابر حجم دارند

            all_rows.append((ts, o, h, l, c, int(vol)))
            price = c

    df = pd.DataFrame(all_rows, columns=["datetime", "open", "high",
                                         "low", "close", "volume"])
    return df
