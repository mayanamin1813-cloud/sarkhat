# -*- coding: utf-8 -*-
"""
دریافت دیتای بورس تهران
=========================
دو تابع ارائه می‌دهد:
  ۱) get_daily_history  : دیتای روزانه (برای روند و فیلترها) — با pytse-client
  ۲) get_intraday_15m   : دیتای ۱۵ دقیقه‌ای روز جاری (برای سرخط‌زنی)

⚠️ نکته مهم: سرورهای TSETMC فقط از داخل ایران قابل دسترسی‌اند.
   اگر این کد را روی VPS خارج از ایران اجرا کنی، خطای شبکه می‌گیری.
"""
import pandas as pd


def get_daily_history(symbol: str, days: int = 180) -> pd.DataFrame:
    """دیتای روزانه نماد با pytse-client (نیاز به نصب: pip install pytse-client)"""
    try:
        from pytse_client import download, symbols_data
    except ImportError:
        raise ImportError("pytse-client نصب نیست. اجرا کن: pip install pytse-client")

    records = download(symbols=[symbol], write_to_csv=False,
                       adjust=True, base_csv_path=None)
    df = records[symbol]
    df = df.reset_index().rename(columns={
        "date": "datetime", "open": "open", "high": "high",
        "low": "low", "close": "close", "volume": "volume",
    })
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df[["datetime", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("datetime").tail(days).reset_index(drop=True)
    return df


def get_intraday_15m(symbol: str) -> pd.DataFrame:
    """
    دیتای لحظه‌ای ۱۵ دقیقه‌ای نماد برای روز جاری.
    از اندپوینت عمومی TSETMC استفاده می‌کند (نیاز به IP ایران).
    اگر در دسترس نبود، پیام راهنما برمی‌گرداند.
    """
    import requests

    # ۱) پیدا کردن کد نماد از جدول نمادها
    try:
        from pytse_client import symbols_data
        ticker_index = symbols_data.get_ticker_index(symbol)
    except Exception:
        raise RuntimeError(f"نماد {symbol} در فهرست نمادها پیدا نشد.")

    # ۲) دریافت معاملات لحظه‌ای (ریز معاملات روز)
    url = f"http://cdn.tsetmc.com/Trade/GetTradeHistory/{ticker_index}/True/0"
    r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    rows = r.json().get("tradeHistory", [])

    if not rows:
        raise RuntimeError("دیتای معاملات امروز خالی است (بازار باز نیست؟)")

    # ۳) تبدیل ریز معاملات به کندل ۱۵ دقیقه‌ای
    df = pd.DataFrame(rows)[["pTran", "qTitTran", "hEven"]]
    df.columns = ["price", "volume", "time_int"]
    t = df["time_int"].astype(str).str.zfill(6)
    df["datetime"] = pd.to_datetime(t.str[:2] + ":" + t.str[2:4] + ":" + t.str[4:6],
                                    format="%H:%M:%S").dt.time
    df = df.sort_values("datetime").set_index("datetime")
    ohlc = df["price"].resample("15min", origin="start").ohlc()
    vol = df["volume"].resample("15min", origin="start").sum()
    out = pd.concat([ohlc, vol], axis=1).dropna()
    out = out.reset_index()
    out.columns = ["time", "open", "high", "low", "close", "volume"]
    return out
