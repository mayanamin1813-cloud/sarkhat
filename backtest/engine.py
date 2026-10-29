# -*- coding: utf-8 -*-
"""
موتور بک‌تست — آزمایش استراتژی روی داده تاریخی
=================================================
این موتور کندل‌به‌کندل پیش می‌رود و دقیقاً همان چیزی را انجام می‌دهد که
ربات زنده انجام می‌دهد: سیگنال از strategy، مجوز از risk_manager،
اجرا از paper_broker. یعنی نتیجه بک‌تست، قابل اتکاترین تخمین عملکرد است.

خروجی: آمار عملکرد + نمودار قیمت/معاملات + نمودار منحنی سرمایه
"""
import os
import pandas as pd
import numpy as np

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.strategy import RangeBreakoutStrategy
from core.risk_manager import RiskManager
from core.paper_broker import PaperBroker
from backtest.charting import setup_persian_font, fa, fmt_toman
import config


def run_backtest(df, market="tse", capital=None, make_chart=True, out_dir=None):
    """
    اجرای بک‌تست روی DataFrame کندل ۱۵ دقیقه‌ای.
    خروجی: دیکشنری آمار + (اختیاری) مسیر نمودار
    """
    capital = capital or config.CAPITAL
    fee = config.FEE_TSE if market == "tse" else config.FEE_CRYPTO
    out_dir = out_dir or config.OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    strat = RangeBreakoutStrategy(
        range_bars=config.RANGE_BARS,
        volume_mult=config.VOLUME_MULT,
        break_buffer=config.BREAK_BUFFER,
        stop_loss_pct=config.STOP_LOSS_PCT,
        take_profit_rr=config.TAKE_PROFIT_RR,
        time_exit_bars=config.TIME_EXIT_BARS,
    )
    risk = RiskManager(
        capital=capital,
        risk_per_trade=config.RISK_PER_TRADE,
        max_daily_loss=config.MAX_DAILY_LOSS,
        max_trades_per_day=config.MAX_TRADES_PER_DAY,
        max_position_pct=config.MAX_POSITION_PCT,
    )
    broker = PaperBroker(capital=capital, fee=fee, slippage=config.SLIPPAGE,
                         log_dir=out_dir, log_name=f"trades_{market}.csv")

    df = df.copy().reset_index(drop=True)
    df["date"] = df["datetime"].dt.date
    entry_idx = None
    entry_qty = 0
    bars_held = 0
    best_close = 0.0        # بالاترین قیمت از لحظه ورود — برای حد ضرر متحرک

    for date, day_df in df.groupby("date"):
        day_df = day_df.reset_index(drop=True)
        risk.start_new_day(date)
        strat.reset()
        n_bars = len(day_df)

        # ایمنی: اگر پوزیشنی از روز قبل مانده، با افتتاح امروز ببند
        if entry_qty > 0:
            first = day_df.iloc[0]
            pnl = broker.sell(entry_qty, float(first["open"]), first["datetime"],
                              "پوزیشن بازمانده از روز قبل")
            risk.register_trade_result(pnl or 0.0)
            entry_qty = 0
            bars_held = 0
            best_close = 0.0
            strat.clear_position()

        for j in range(n_bars):
            bar = day_df.iloc[j]
            close = float(bar["close"])
            broker.record_equity(bar["datetime"], close)

            # ۱) مدیریت خروج برای پوزیشن باز (اولویت با خروج)
            if entry_qty > 0:
                bars_held += 1
                best_close = max(best_close, close)
                exit_reason = None
                exit_price = None

                # حد ضرر متحرک: بزرگ‌ترینِ (ثابت، متحرک)
                eff_stop = strat.stop_loss
                if config.USE_TRAILING:
                    eff_stop = max(strat.stop_loss,
                                   best_close * (1 - config.TRAIL_PCT))

                if float(bar["low"]) <= eff_stop:
                    exit_reason = "حد ضرر متحرک" if eff_stop > strat.stop_loss else "حد ضرر"
                    exit_price = eff_stop
                elif float(bar["high"]) >= strat.take_profit:
                    exit_reason, exit_price = "حد سود", strat.take_profit
                elif close < strat.range_high:
                    # خروج ساختاری: قیمت زیر سطح شکست برگشت (شکست ناموفق)
                    exit_reason, exit_price = "ریزش زیر سطح شکست", close
                elif j == n_bars - 1:                      # پایان جلسه
                    exit_reason, exit_price = "پایان جلسه", close
                elif bars_held >= config.TIME_EXIT_BARS:
                    exit_reason, exit_price = "خروج زمانی", close

                if exit_reason:
                    pnl = broker.sell(entry_qty, exit_price, bar["datetime"], exit_reason)
                    risk.register_trade_result(pnl or 0.0)
                    entry_qty = 0
                    entry_idx = None
                    bars_held = 0
                    best_close = 0.0
                    strat.clear_position()

            # ۲) ساخت محدوده اولیه در کندل‌های ابتدای جلسه
            if j < config.RANGE_BARS:
                if j == config.RANGE_BARS - 1:
                    strat.update_range(day_df.iloc[: config.RANGE_BARS])
                continue

            # ۳) بررسی سیگنال ورود (۲ کندل آخر جلسه ورود نداریم — زمان کافی برای خروج نیست)
            if entry_qty > 0 or risk.state.halted:
                continue
            if j >= n_bars - 2:
                continue
            # خط پایه حجم: میانگین حجم ۲۰ کندل قبلی جلسه (حداقل ۴ کندل)
            base_start = max(0, j - 20)
            baseline_volume = float(day_df.iloc[base_start:j]["volume"].mean())
            sig = strat.on_bar(j, bar, j, baseline_volume)

            if sig.action == "buy":
                allowed, why = risk.can_open(broker.invested_value)
                if not allowed:
                    continue
                qty, _ = risk.position_size(close, sig.stop_loss)
                if qty > 0 and broker.buy(qty, close, bar["datetime"], sig.reason):
                    entry_qty = qty
                    entry_idx = j
                    bars_held = 0
                    best_close = close
                    strat.entry_price = close
                    strat.stop_loss = sig.stop_loss
                    strat.take_profit = sig.take_profit

    # ----------------------------------------------------------
    # آمار نهایی
    # ----------------------------------------------------------
    stats = broker.stats()
    final_equity = broker.total_equity(float(df.iloc[-1]["close"]))
    stats["سرمایه اولیه"] = capital
    stats["سرمایه نهایی"] = round(final_equity, 0)
    stats["بازده کل"] = f"{((final_equity / capital - 1) * 100):.1f}٪"
    stats["بازده سالانه تقریبی"] = f"{((final_equity / capital - 1) * 100 * 365 / (len(df) / (96 if market == 'crypto' else 26))):.0f}٪"

    eq = pd.Series([e[1] for e in broker.equity_curve])
    running_max = eq.cummax()
    dd = (eq - running_max) / running_max
    stats["حداکثر افت سرمایه (Drawdown)"] = f"{dd.min() * 100:.1f}٪"

    csv_path = broker.save_trades_csv()
    chart_path = None
    if make_chart:
        chart_path = make_backtest_chart(df, broker, market, out_dir)

    return stats, chart_path, csv_path


# --------------------------------------------------------------
def make_backtest_chart(df, broker, market, out_dir):
    """نمودار دومرحله‌ای: بالا قیمت + نقاط خرید/فروش، پایین منحنی سرمایه"""
    setup_persian_font()
    import matplotlib.pyplot as plt

    buys = [(t["time"], t["price"]) for t in broker.trades if t["side"] == "خرید"]
    sells = [(t["time"], t["price"]) for t in broker.trades if t["side"] == "فروش"]
    label = "بورس تهران (داده نمونه)" if market == "tse" else "کریپتو/نوبیتکس (داده نمونه)"

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=False,
                                   constrained_layout=True,
                                   height_ratios=[2, 1.2])
    ax1.plot(df["datetime"], df["close"], lw=0.7, color="#5B6B7D", label=fa("قیمت"))
    if buys:
        bt, bp = zip(*buys)
        ax1.scatter(pd.to_datetime(bt), bp, marker="^", s=42, color="#2E8B57",
                    label=fa("خرید"), zorder=3)
    if sells:
        st, sp = zip(*sells)
        ax1.scatter(pd.to_datetime(st), sp, marker="v", s=42, color="#C0392B",
                    label=fa("فروش"), zorder=3)
    ax1.set_title(fa(f"بک‌تست استراتژی شکست محدوده — {label}"), fontsize=13, fontweight="bold")
    ax1.set_ylabel(fa("قیمت (تومان)"))
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(alpha=0.25)

    eq_t = [t for t, _ in broker.equity_curve]
    eq_v = [v for _, v in broker.equity_curve]
    ax2.plot(eq_t, eq_v, lw=1.4, color="#C9A84C")
    ax2.set_title(fa("منحنی ارزش حساب (Equity Curve)"), fontsize=11)
    ax2.set_ylabel(fa("ارزش حساب"))
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: fmt_toman(x)))
    ax2.grid(alpha=0.25)

    path = os.path.join(out_dir, f"backtest_{market}.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# --------------------------------------------------------------
def print_stats(stats: dict):
    """چاپ آمار به شکل جدول خوانا در ترمینال"""
    print("\n" + "=" * 52)
    print("   نتیجه بک‌تست استراتژی سرخط‌زن")
    print("=" * 52)
    for k, v in stats.items():
        print(f"  {k:<28} {v}")
    print("=" * 52 + "\n")
