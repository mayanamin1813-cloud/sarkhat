# -*- coding: utf-8 -*-
"""
ماژول استراتژی «شکست محدوده ابتدای جلسه» (سرخط‌زنی چند نوبتی)
================================================================
منطق کلی:
  ۱) اول «RANGE_BARS» کندلِ ۱۵ دقیقه‌ای ابتدای جلسه را می‌گیریم و
     سقف و کف آن را «محدوده اولیه» می‌نامیم.
  ۲) وقتی قیمت با حجم بالا سقف محدوده را می‌شکند (سر خط خرید می‌زنیم).
  ۳) حد ضرر زیر کف محدوده، حد سود = ۲ برابر ریسک.
  ۴) اگر تا پایان «TIME_EXIT_BARS» کندل معامله بسته نشد، خروج زمانی.

خروجی این ماژول فقط «سیگنال» است؛ اجرا و مدیریت پول با ماژول‌های دیگر.
"""
import pandas as pd
from dataclasses import dataclass


@dataclass
class Signal:
    """یک سیگنال معاملاتی ساده"""
    action: str          # "buy" یا "sell" یا "hold"
    reason: str = ""     # دلیل سیگنال برای لاگ و یادگیری
    stop_loss: float = 0.0
    take_profit: float = 0.0


class RangeBreakoutStrategy:
    """استراتژی شکست محدوده ابتدای جلسه روی کندل‌های ۱۵ دقیقه‌ای"""

    def __init__(self, range_bars=4, volume_mult=1.5, break_buffer=0.002,
                 stop_loss_pct=0.015, take_profit_rr=2.0, time_exit_bars=16):
        self.range_bars = range_bars
        self.volume_mult = volume_mult
        self.break_buffer = break_buffer
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_rr = take_profit_rr
        self.time_exit_bars = time_exit_bars
        self.reset()

    def reset(self):
        """شروع جلسه جدید — محدوده و وضعیت پاک می‌شود"""
        self.range_high = None
        self.range_low = None
        self.avg_volume = None
        self.entry_index = None
        self.entry_price = None
        self.stop_loss = None
        self.take_profit = None

    def clear_position(self):
        """بعد از بسته شدن معامله صدا زده می‌شود — پوزیشن پاک می‌شود ولی محدوده می‌ماند"""
        self.entry_price = None
        self.entry_index = None
        self.stop_loss = None
        self.take_profit = None

    # ----------------------------------------------------------
    def update_range(self, window):
        """
        محدوده اولیه را از کندل‌های ابتدای جلسه محاسبه می‌کند.
        window: DataFrame یا لیست دیکشنری حاوی RANGE_BARS کندل اول جلسه
        """
        if hasattr(window, "iloc"):          # pandas DataFrame
            self.range_high = float(window["high"].max())
            self.range_low = float(window["low"].min())
            self.avg_volume = float(window["volume"].mean())
        else:                                # لیست دیکشنری (حالت paper)
            self.range_high = max(float(b["high"]) for b in window)
            self.range_low = min(float(b["low"]) for b in window)
            self.avg_volume = sum(float(b["volume"]) for b in window) / len(window)

    # ----------------------------------------------------------
    def on_bar(self, i, bar, session_bar_number, baseline_volume):
        """
        هر کندل جدید را پردازش می‌کند و سیگنال برمی‌گرداند.
          i                  : شماره کندل در کل داده
          bar                : ردیف کندل جاری (open/high/low/close/volume)
          session_bar_number : چندمین کندل از ابتدای جلسه امروز است
          baseline_volume    : میانگین حجم کندل‌های قبلی (خط پایه مقایسه حجم)
        """
        price = float(bar["close"])

        # مرحله ۱: ساخت محدوده اولیه
        if session_bar_number < self.range_bars:
            return Signal("hold", "در حال ساخت محدوده اولیه")

        # اگر محدوده هنوز مقداردهی نشده (شروع جلسه) با داده موجود بساز
        if self.range_high is None:
            return Signal("hold", "محدوده آماده نیست")

        # اگر پوزیشن باز داریم → مدیریت خروج در backtest/engine انجام می‌شود
        if self.entry_price is not None:
            return self._check_exit(bar)

        # مرحله ۲: شرط ورود — شکست سقف محدوده با تأیید حجم
        trigger = self.range_high * (1 + self.break_buffer)
        if price > trigger:
            # تأیید حجم: حجم کندل شکست باید حداقل VOLUME_MULT برابر میانگین قبلی باشد
            vol_ok = baseline_volume and float(bar["volume"]) > self.volume_mult * baseline_volume
            if vol_ok:
                self.entry_price = price
                self.entry_index = i
                # حد ضرر: زیر کف محدوده، ولی حداقل ۱.۵٪ زیر ورود (مقاوم به نویز)
                self.stop_loss = min(self.range_low, price * (1 - self.stop_loss_pct))
                self.take_profit = price + self.take_profit_rr * (price - self.stop_loss)
                return Signal("buy", f"شکست سقف محدوده {self.range_high:,.0f} با تأیید حجم",
                              self.stop_loss, self.take_profit)
            return Signal("hold", "شکست بدون تأیید حجم — صرف‌نظر شد")

        return Signal("hold", "منتظر شکست معتبر")

    # ----------------------------------------------------------
    def _check_exit(self, bar):
        """بررسی حد ضرر / حد سود / خروج زمانی برای پوزیشن باز"""
        bars_held = None
        if self.entry_index is not None:
            bars_held = None  # موتور بک‌تست خودش می‌شمارد؛ اینجا فقط قیمت‌ها
        low, high, close = float(bar["low"]), float(bar["high"]), float(bar["close"])

        # ترتیب محافظه‌کارانه: اول حد ضرر
        if low <= self.stop_loss:
            return Signal("sell", f"برخورد حد ضرر {self.stop_loss:,.0f}")
        if high >= self.take_profit:
            return Signal("sell", f"برخورد حد سود {self.take_profit:,.0f}")
        return Signal("hold", "پوزیشن باز")
