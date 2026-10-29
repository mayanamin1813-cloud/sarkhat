# -*- coding: utf-8 -*-
"""
ابزار نمودار فارسی
====================
matplotlib به‌صورت پیش‌فرض متن فارسی را خراب نشان می‌دهد (حروف جدا و چپ‌به‌راست).
این ماژول با «reshaper» حروف را به شکل درست تبدیل می‌کند و فونت فارسی پیدا می‌کند.
"""
import os
import arabic_reshaper
from bidi.algorithm import get_display

import matplotlib
import matplotlib.font_manager as fm

_FONT_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "..", "fonts", "Vazirmatn-Regular.ttf"),
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "fonts", "Vazirmatn-Regular.ttf"),
    "C:/Windows/Fonts/tahoma.ttf",            # ویندوز
    "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf",
]

_font_loaded = False


def setup_persian_font():
    """فونت فارسی را یک‌بار به matplotlib معرفی می‌کند"""
    global _font_loaded
    if _font_loaded:
        return
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            fm.fontManager.addfont(path)
            name = fm.FontProperties(fname=path).get_name()
            matplotlib.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            _font_loaded = True
            return
    # هیچ فونت فارسی پیدا نشد — نمودار کار می‌کند ولی متن فارسی خراب دیده می‌شود
    print("⚠️ فونت فارسی پیدا نشد؛ فونت Vazirmatn را نصب کن (پوشه fonts)")


def fa(text: str) -> str:
    """تبدیل متن فارسی برای نمایش صحیح در matplotlib"""
    return get_display(arabic_reshaper.reshape(str(text)))


def fmt_toman(value: float) -> str:
    """نمایش اعداد بزرگ به شکل خوانا"""
    if abs(value) >= 1e9:
        return f"{value/1e9:,.1f} میلیارد"
    if abs(value) >= 1e6:
        return f"{value/1e6:,.0f} میلیون"
    return f"{value:,.0f}"
