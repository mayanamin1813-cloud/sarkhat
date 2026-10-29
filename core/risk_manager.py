# -*- coding: utf-8 -*-
"""
ماژول مدیریت ریسک — محافظ جان ربات
====================================
این ماژول قبل از هر سفارش تصمیم می‌گیرد که آیا معامله مجاز است یا نه،
و حجم معامله را محاسبه می‌کند. قوانین ریسک قابل مذاکره نیستند!
"""
from dataclasses import dataclass, field
import datetime


@dataclass
class RiskState:
    """وضعیت ریسک روز جاری"""
    day: str = ""
    trades_today: int = 0
    realized_pnl_today: float = 0.0
    halted: bool = False          # وقتی True شود، ربات تا روز بعد معامله نمی‌کند


class RiskManager:
    """کنترل حجم پوزیشن، سقف ضرر روزانه و تعداد معاملات"""

    def __init__(self, capital, risk_per_trade=0.01, max_daily_loss=0.02,
                 max_trades_per_day=3, max_position_pct=0.25):
        self.capital = float(capital)
        self.risk_per_trade = risk_per_trade
        self.max_daily_loss = max_daily_loss
        self.max_trades_per_day = max_trades_per_day
        self.max_position_pct = max_position_pct
        self.state = RiskState()

    # ----------------------------------------------------------
    def start_new_day(self, day_label: str):
        """ابتدای روز معاملاتی — شمارنده‌ها صفر می‌شوند"""
        self.state = RiskState(day=str(day_label))

    # ----------------------------------------------------------
    def register_trade_result(self, pnl: float):
        """بعد از بسته شدن هر معامله، نتیجه ثبت می‌شود"""
        self.state.trades_today += 1
        self.state.realized_pnl_today += pnl
        # سقف ضرر روزانه: کلید قطع اضطراری
        if self.state.realized_pnl_today <= -self.max_daily_loss * self.capital:
            self.state.halted = True

    # ----------------------------------------------------------
    def can_open(self, current_invested: float = 0.0):
        """
        آیا اجازه معامله جدید داریم؟
        برمی‌گرداند: (اجازه؟, دلیل)
        """
        if self.state.halted:
            return False, "توقف روزانه: سقف ضرر پر شده است"
        if self.state.trades_today >= self.max_trades_per_day:
            return False, f"سقف {self.max_trades_per_day} معامله در روز پر شد"
        if current_invested >= self.max_position_pct * self.capital:
            return False, "سقف درگیری سرمایه (۲۵٪) پر است"
        return True, "مجاز"

    # ----------------------------------------------------------
    def position_size(self, entry_price: float, stop_loss: float):
        """
        محاسبه حجم معامله بر اساس ریسک ثابت:
          مبلغ ریسک = ۱٪ سرمایه
          تعداد = مبلغ ریسک ÷ (قیمت ورود − حد ضرر)
        حجم نهایی با سقف «حداکثر درگیری سرمایه» محدود می‌شود.
        """
        risk_amount = self.risk_per_trade * self.capital
        stop_distance = max(entry_price - stop_loss, entry_price * 0.002)
        qty = risk_amount / stop_distance
        max_qty = (self.max_position_pct * self.capital) / entry_price
        qty = min(qty, max_qty)
        return int(qty), risk_amount

    # ----------------------------------------------------------
    def summary(self) -> str:
        s = self.state
        return (f"روز {s.day} | معاملات: {s.trades_today} | "
                f"سود/زیان روز: {s.realized_pnl_today:,.0f} | "
                f"وضعیت: {'متوقف' if s.halted else 'فعال'}")
