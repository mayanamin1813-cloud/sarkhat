# -*- coding: utf-8 -*-
"""
دریافت دیتای کریپتو از نوبیتکس (عمومی — بدون نیاز به کلید)
============================================================
اندپوینت UDF history نوبیتکس، کندل‌های تاریخی می‌دهد.
این اندپوینت معمولاً از داخل ایران قابل دسترسی است.
"""
import time
import requests
import pandas as pd


def get_crypto_ohlcv(symbol="BTCIRT", resolution=15, days=60) -> pd.DataFrame:
    """
    دریافت کندل‌های تاریخی از نوبیتکس.
      symbol    : BTCIRT, ETHIRT, USDTIRT و ...
      resolution: 15 = ۱۵ دقیقه، 60 = یک ساعته، D = روزانه
      days      : عمق تاریخی به روز
    """
    to_ts = int(time.time())
    from_ts = to_ts - days * 86400
    url = "https://api.nobitex.ir/v2/udf/history"
    params = {"symbol": symbol, "resolution": resolution,
              "from": from_ts, "to": to_ts}
    r = requests.get(url, params=params, timeout=15,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    if data.get("s") != "ok" or not data.get("c"):
        raise RuntimeError(f"دیتای {symbol} دریافت نشد: {data}")

    df = pd.DataFrame({
        "datetime": pd.to_datetime(data["t"], unit="s"),
        "open": data["o"], "high": data["h"],
        "low": data["l"], "close": data["c"],
        "volume": data["v"],
    }).astype({"open": float, "high": float, "low": float,
               "close": float, "volume": float})
    return df
