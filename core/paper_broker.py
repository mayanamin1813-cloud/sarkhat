# -*- coding: utf-8 -*-
"""
بروکر شبیه‌سازی (Paper Broker)
================================
معاملات را دقیقاً مثل یک بروکر واقعی شبیه‌سازی می‌کند:
  - اجرا با لغزش قیمت (slippage)
  - کسر کارمزد
  - ثبت کامل لاگ معاملات در CSV
هیچ پول واقعی جابه‌جا نمی‌شود — برای تمرین و اعتبارسنجی استراتژی عالی است.
"""
import os
import csv
import datetime


class PaperBroker:
    def __init__(self, capital, fee=0.015, slippage=0.002, log_dir="output", log_name="trades_paper.csv"):
        self.initial_capital = float(capital)
        self.cash = float(capital)
        self.fee = fee                    # هزینه هر معامله (خرید یا فروش)
        self.slippage = slippage
        self.position_qty = 0             # تعداد سهم/ارز در اختیار
        self.position_avg_price = 0.0
        self.trades = []                  # لیست معاملات بسته‌شده
        self.equity_curve = []            # ارزش کل حساب در هر کندل
        os.makedirs(log_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, log_name)

    # ----------------------------------------------------------
    @property
    def invested_value(self):
        return self.position_qty * self.position_avg_price

    def total_equity(self, price):
        """ارزش کل حساب = نقد + ارزش پوزیشن باز"""
        return self.cash + self.position_qty * price

    # ----------------------------------------------------------
    def buy(self, qty, price, when, reason=""):
        """خرید با لغزش و کارمزد"""
        if qty <= 0:
            return False
        fill_price = price * (1 + self.slippage)   # خرید کمی گران‌تر اجرا می‌شود
        cost = qty * fill_price
        fee = cost * self.fee
        if cost + fee > self.cash:
            qty = int(self.cash / (fill_price * (1 + self.fee)))
            if qty <= 0:
                return False
            cost = qty * fill_price
            fee = cost * self.fee
        # میانگین‌گیری اگر پوزیشن از قبل هست
        total_qty = self.position_qty + qty
        self.position_avg_price = ((self.position_avg_price * self.position_qty) + cost) / total_qty
        self.position_qty = total_qty
        self.cash -= (cost + fee)
        self.trades.append({
            "time": str(when), "side": "خرید", "qty": qty,
            "price": round(fill_price, 0), "fee": round(fee, 0),
            "pnl": "", "reason": reason,
        })
        return True

    # ----------------------------------------------------------
    def sell(self, qty, price, when, reason=""):
        """فروش (بستن پوزیشن) با لغزش و کارمزد"""
        if self.position_qty <= 0:
            return False
        qty = min(qty, self.position_qty)
        fill_price = price * (1 - self.slippage)   # فروش کمی ارزان‌تر اجرا می‌شود
        proceeds = qty * fill_price
        fee = proceeds * self.fee
        self.cash += (proceeds - fee)
        # سود/زیان این معامله
        pnl = qty * (fill_price - self.position_avg_price) - fee
        self.trades.append({
            "time": str(when), "side": "فروش", "qty": qty,
            "price": round(fill_price, 0), "fee": round(fee, 0),
            "pnl": round(pnl, 0), "reason": reason,
        })
        self.position_qty -= qty
        if self.position_qty == 0:
            self.position_avg_price = 0.0
        return pnl

    # ----------------------------------------------------------
    def record_equity(self, when, price):
        self.equity_curve.append((when, self.total_equity(price)))

    # ----------------------------------------------------------
    def save_trades_csv(self):
        if not self.trades:
            return None
        with open(self.log_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=self.trades[0].keys())
            writer.writeheader()
            writer.writerows(self.trades)
        return self.log_path

    # ----------------------------------------------------------
    def stats(self):
        """آمار عملکرد برای نمایش در پایان بک‌تست"""
        closed = [t for t in self.trades if t["side"] == "فروش"]
        pnls = [t["pnl"] for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "تعداد معاملات بسته‌شده": len(closed),
            "تعداد برد": len(wins),
            "تعداد باخت": len(losses),
            "نرخ برد": f"{(len(wins) / len(closed) * 100):.1f}٪" if closed else "-",
            "مجموع سود": round(gross_win, 0),
            "مجموع ضرر": round(gross_loss, 0),
            "فاکتور سود (Profit Factor)": f"{(gross_win / gross_loss):.2f}" if gross_loss else "∞",
            "میانگین سود هر معامله": round(sum(pnls) / len(pnls), 0) if pnls else 0,
        }
