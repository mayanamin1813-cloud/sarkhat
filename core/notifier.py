# -*- coding: utf-8 -*-
"""
ماژول اعلان تلگرام (اختیاری)
==============================
اگر توکن ربات و chat_id را در config.py بگذاری، هر معامله به تلگرامت پیام می‌رود.
اگر خالی بگذاری، همه چیز بدون تلگرام هم کار می‌کند.
"""
import requests


class TelegramNotifier:
    def __init__(self, bot_token="", chat_id=""):
        self.enabled = bool(bot_token and chat_id)
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, text: str):
        """ارسال پیام — اگر غیرفعال بود، بی‌صدا رد می‌شود"""
        if not self.enabled:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            r = requests.post(url, json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
            }, timeout=10)
            return r.ok
        except Exception:
            # اعلان تلگرام هرگز نباید ربات را متوقف کند
            return False
