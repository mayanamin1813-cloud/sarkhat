# -*- coding: utf-8 -*-
"""
آداپتور صرافی نوبیتکس (معامله واقعی کریپتو)
==============================================
⚠️ هشدار: این ماژول با پول واقعی کار می‌کند.
   فقط وقتی فعالش کن که: ۱) بک‌تست را دیده‌ای  ۲) حداقل ۱ ماه paper تست کردی
   ۳) با مبالغ خیلی کم شروع می‌کنی  ۴) کلید API فقط مجوز معامله دارد (بدون برداشت)

مستندات رسمی: https://api.nobitex.ir
نکته: اگر سرویس نوبیتکس فیلدها را تغییر داد، این فایل را بر اساس مستندات روز
به‌روزرسانی کن. ساختار کد طوری است که فقط این فایل تغییر می‌کند.
"""
import requests

BASE_URL = "https://api.nobitex.ir"


class NobitexBroker:
    """اجرا و لغو سفارش واقعی روی صرافی نوبیتکس"""

    def __init__(self, api_key, api_secret=""):
        if not api_key:
            raise ValueError("کلید API نوبیتکس خالی است! در config.py مقدار NOBITEX_API_KEY را پر کن.")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        })

    # ----------------------------------------------------------
    # داده بازار (عمومی — بدون نیاز به کلید)
    # ----------------------------------------------------------
    @staticmethod
    def get_ohlcv(symbol="BTCIRT", resolution=15, days=30):
        """
        دریافت کندل‌های نماد.
          resolution: 15 = ۱۵ دقیقه‌ای، 60 = ساعتی، D = روزانه
        خروجی: DataFrame با ستون‌های open/high/low/close/volume
        """
        import time
        import pandas as pd
        to_ts = int(time.time())
        from_ts = to_ts - days * 86400
        url = f"{BASE_URL}/v2/udf/history"
        params = {"symbol": symbol, "resolution": resolution,
                  "from": from_ts, "to": to_ts}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("s") != "ok" or not data.get("c"):
            raise RuntimeError(f"دیتای {symbol} دریافت نشد: {data}")
        df = pd.DataFrame({
            "datetime": pd.to_datetime(data["t"], unit="s"),
            "open": data["o"], "high": data["h"],
            "low": data["l"], "close": data["c"],
            "volume": data["v"],
        })
        return df

    # ----------------------------------------------------------
    # موجودی حساب
    # ----------------------------------------------------------
    def get_balances(self):
        """لیست موجودی کیف پول‌ها"""
        r = self.session.post(f"{BASE_URL}/users/wallets/list", timeout=15)
        r.raise_for_status()
        return r.json().get("wallets", [])

    # ----------------------------------------------------------
    # ثبت سفارش
    # ----------------------------------------------------------
    def place_limit_order(self, side, symbol, amount, price):
        """
        ثبت سفارش لیمیت.
          side: "buy" یا "sell"
          symbol: مثل "BTCIRT"
          amount: مقدار ارز (مثلاً 0.001 بیت‌کوین)
          price: قیمت تومان
        """
        payload = {
            "type": side,
            "srcCurrency": symbol[:-3].lower(),   # BTCIRT → btc
            "dstCurrency": "rls",
            "amount": str(amount),
            "price": str(int(price)),
            "execution": "limit",
        }
        r = self.session.post(f"{BASE_URL}/users/market/orders/limit",
                              json=payload, timeout=15)
        r.raise_for_status()
        result = r.json()
        if result.get("status") != "done":
            raise RuntimeError(f"سفارش ثبت نشد: {result}")
        return result

    def cancel_order(self, order_id):
        """لغو سفارش باز"""
        r = self.session.post(f"{BASE_URL}/users/market/orders/cancel",
                              json={"orderId": order_id}, timeout=15)
        r.raise_for_status()
        return r.json()

    def get_open_orders(self, symbol="BTCIRT"):
        """لیست سفارش‌های باز"""
        r = self.session.post(f"{BASE_URL}/users/orders/list",
                              json={"details": True, "isOpen": True,
                                    "srcCurrency": symbol[:-3].lower(),
                                    "dstCurrency": "rls"}, timeout=15)
        r.raise_for_status()
        return r.json().get("orders", [])
