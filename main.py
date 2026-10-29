# -*- coding: utf-8 -*-
"""
ربات سرخط‌زن — نقطه ورود اصلی
================================
نحوه اجرا:

  python main.py backtest      ← آزمایش استراتژی روی داده تاریخی (بدون ریسک)
  python main.py paper         ← کارکرد زنده با پول شبیه‌سازی‌شده (بدون ریسک)
  python main.py serve         ← سرور + اپلیکیشن موبایل (سیگنال واقعی روی گوشی!)
  python main.py live          ← معامله واقعی (فقط بعد از فعال‌سازی API کارگزاری!)

ترتیب پیشنهادی: ۱ ماه backtest و paper قبل از حتی فکر کردن به live.
"""
import os
import sys
import time
import argparse
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from core.strategy import RangeBreakoutStrategy
from core.risk_manager import RiskManager
from core.paper_broker import PaperBroker
from core.notifier import TelegramNotifier
# توجه: ماژول نمودار (backtest.charting) فقط داخل run_backtest_mode و به‌صورت تنبل
# بارگذاری می‌شود تا روی گوشی (بدون matplotlib) هم همه حالت‌های دیگر کار کنند.


# ==============================================================
def run_backtest_mode(market="tse", days=90, capital=None):
    from backtest.sample_data import generate_sample_15m
    from backtest.engine import run_backtest, print_stats

    markets = ["tse", "crypto"] if market == "both" else [market]
    for m in markets:
        print(f"\n⏳ در حال اجرای بک‌تست برای بازار: "
              f"{'بورس تهران' if m == 'tse' else 'کریپتو'} ...")
        df = generate_sample_15m(days=days, market=m,
                                 start_price=100_000 if m == "tse" else 500_000)
        stats, chart, csv_path = run_backtest(df, market=m, capital=capital)
        print_stats(stats)
        if chart:
            print(f"  📊 نمودار: {chart}")
        if csv_path:
            print(f"  📁 لاگ معاملات: {csv_path}")


# ==============================================================
def run_paper_mode(market="tse"):
    """
    حالت پیپرتریدینگ (شبیه‌سازی زنده):
      - هر ۱۵ دقیقه یک کندل جدید می‌گیرد
      - سیگنال می‌سازد و روی بروکر شبیه‌سازی ثبت می‌کند
      - اگر اینترنت/دیتا نبود، با کندل شبیه‌سازی‌شده ادامه می‌دهد
        تا بتوانی رفتار ربات را همین امروز ببینی.
    """
    from backtest.sample_data import generate_sample_15m

    print("=" * 60)
    print("  ربات سرخط‌زن — حالت شبیه‌سازی زنده (paper)")
    print("  هیچ سفارش واقعی ثبت نمی‌شود. برای خروج Ctrl+C بزن.")
    print("=" * 60)

    strat = RangeBreakoutStrategy(
        range_bars=config.RANGE_BARS, volume_mult=config.VOLUME_MULT,
        break_buffer=config.BREAK_BUFFER, stop_loss_pct=config.STOP_LOSS_PCT,
        take_profit_rr=config.TAKE_PROFIT_RR, time_exit_bars=config.TIME_EXIT_BARS)
    risk = RiskManager(capital=config.CAPITAL,
                       risk_per_trade=config.RISK_PER_TRADE,
                       max_daily_loss=config.MAX_DAILY_LOSS,
                       max_trades_per_day=config.MAX_TRADES_PER_DAY,
                       max_position_pct=config.MAX_POSITION_PCT)
    broker = PaperBroker(capital=config.CAPITAL, fee=config.FEE_TSE,
                         slippage=config.SLIPPAGE, log_dir=config.OUTPUT_DIR)
    notify = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)

    # حلقه اصلی — هر ۱۵ دقیقه (برای تست می‌توانی INTERVAL را کوچک‌تر کنی)
    INTERVAL_SECONDS = 5          # حالت نمایشی: هر ۵ ثانیه یک کندل «فرضی»
    USE_REAL_DATA = False         # True کن اگر دیتای زنده داری (IP ایران)

    if USE_REAL_DATA:
        from core.data_tsetmc import get_intraday_15m
    else:
        # داده نمونه به‌صورت جریانی — فقط برای دیدن رفتار ربات
        demo_df = generate_sample_15m(days=3, market="tse", seed=7)
        demo_iter = iter(demo_df.to_dict("records"))

    current_day = None
    session_bars = []
    pos_bars = 0          # چند کندل از باز شدن پوزیشن گذشته

    try:
        while True:
            # ---------- ۱) گرفتن کندل جدید ----------
            if USE_REAL_DATA:
                try:
                    df15 = get_intraday_15m(config.TSE_SYMBOLS[0])
                    latest = df15.iloc[-1].to_dict()
                    latest["datetime"] = df15.iloc[-1].get("time")
                except Exception as e:
                    print(f"⚠️ دیتای زنده در دسترس نیست ({e.__class__.__name__}) — ۱۵ دقیقه بعد تلاش می‌کنیم")
                    time.sleep(900)
                    continue
            else:
                try:
                    row = next(demo_iter)
                except StopIteration:
                    print("\n✅ داده نمایشی تمام شد. لاگ معاملات ذخیره شد.")
                    break
                latest = {"datetime": row["datetime"], "open": row["open"],
                          "high": row["high"], "low": row["low"],
                          "close": row["close"], "volume": row["volume"]}

            day = pd_day = str(latest["datetime"])[:10]
            if day != current_day:
                # پایان جلسه: پوزیشن باز را با قیمت فعلی ببند
                if broker.position_qty > 0:
                    pnl = broker.sell(broker.position_qty, float(latest["close"]),
                                      latest["datetime"], "پایان جلسه")
                    risk.register_trade_result(pnl or 0)
                    strat.clear_position()
                    pos_bars = 0
                current_day = day
                risk.start_new_day(day)
                strat.reset()
                session_bars = []
                print(f"\n📅 روز جدید: {day} | {risk.summary()}")

            session_bars.append(latest)
            j = len(session_bars) - 1
            bar = latest

            # ---------- ۲) مدیریت پوزیشن باز ----------
            if broker.position_qty > 0:
                pos_bars += 1
                close_now = float(bar["close"])
                if float(bar["low"]) <= strat.stop_loss:
                    pnl = broker.sell(broker.position_qty, strat.stop_loss,
                                      bar["datetime"], "حد ضرر")
                    risk.register_trade_result(pnl or 0)
                    strat.clear_position()
                    pos_bars = 0
                    notify.send(f"🔻 فروش (حد ضرر): {pnl:,.0f} تومان")
                elif float(bar["high"]) >= strat.take_profit:
                    pnl = broker.sell(broker.position_qty, strat.take_profit,
                                      bar["datetime"], "حد سود")
                    risk.register_trade_result(pnl or 0)
                    strat.clear_position()
                    pos_bars = 0
                    notify.send(f"🔺 فروش (حد سود): {pnl:,.0f} تومان")
                elif close_now < strat.range_high:
                    pnl = broker.sell(broker.position_qty, close_now,
                                      bar["datetime"], "ریزش زیر سطح شکست")
                    risk.register_trade_result(pnl or 0)
                    strat.clear_position()
                    pos_bars = 0
                    notify.send(f"↘️ فروش (شکست ناموفق): {pnl:,.0f} تومان")
                elif pos_bars >= config.TIME_EXIT_BARS:
                    pnl = broker.sell(broker.position_qty, close_now,
                                      bar["datetime"], "خروج زمانی")
                    risk.register_trade_result(pnl or 0)
                    strat.clear_position()
                    pos_bars = 0
                    notify.send(f"⏱ فروش (خروج زمانی): {pnl:,.0f} تومان")

            # ---------- ۳) سیگنال ورود ----------
            if broker.position_qty == 0 and not risk.state.halted and j + 1 >= config.RANGE_BARS:
                strat.update_range(session_bars[:config.RANGE_BARS])
                base_start = max(0, j - 20)
                baseline_volume = sum(b["volume"] for b in session_bars[base_start:j]) / max(j - base_start, 1)
                sig = strat.on_bar(j, bar, j, baseline_volume)
                if sig.action == "buy":
                    allowed, why = risk.can_open(broker.invested_value)
                    if allowed:
                        qty, _ = risk.position_size(float(bar["close"]), sig.stop_loss)
                        if broker.buy(qty, float(bar["close"]), bar["datetime"], sig.reason):
                            strat.entry_price = float(bar["close"])
                            strat.stop_loss = sig.stop_loss
                            strat.take_profit = sig.take_profit
                            pos_bars = 0
                            msg = (f"🟢 خرید {qty} عدد در {bar['close']:,.0f}\n"
                                   f"حد ضرر: {sig.stop_loss:,.0f} | حد سود: {sig.take_profit:,.0f}")
                            print(f"  {msg}")
                            notify.send(msg)
                    else:
                        print(f"  ⛔ ریسک اجازه نداد: {why}")

            broker.record_equity(bar["datetime"], float(bar["close"]))
            time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\n⏹ توقف توسط کاربر")

    broker.save_trades_csv()
    eq = broker.total_equity(float(bar["close"]))
    print(f"\n💰 ارزش نهایی حساب (شبیه‌سازی): {eq:,.0f} تومان")
    print(f"📁 لاگ معاملات: {config.OUTPUT_DIR}/trades_paper.csv")


# ==============================================================
def run_live_mode():
    """حالت معامله واقعی — با چند لایه قفل ایمنی"""
    print("=" * 60)
    print("  ⚠️  حالت معامله واقعی (LIVE)")
    print("=" * 60)

    if not config.ENABLE_LIVE:
        print("\n❌ در config.py مقدار ENABLE_LIVE برابر False است.")
        print("   این قفل عمدی است! قبل از فعال‌سازی:")
        print("   ۱) بک‌تست و پیپرتریدینگ را حداقل یک ماه اجرا کن")
        print("   ۲) API کارگزاری را فعال و الگوریتم را ثبت کن")
        print("   ۳) با کمترین سرمایه شروع کن")
        return

    if config.MODE == "paper":
        print("\n❌ MODE در config.py روی paper است. برای live مقدار MODE='live' کن.")
        return

    # کریپتو: کلید نوبیتکس لازم است
    if not config.NOBITEX_API_KEY:
        print("\n❌ NOBITEX_API_KEY در config.py خالی است.")
        return

    print("\n🚀 فعال‌سازی live فقط برای کریپتو آماده است (نوبیتکس).")
    print("   برای بورس تهران، اول core/tse_broker.py را با API کارگزاری‌ات کامل کن.")
    print("\nگام بعدی: در main.py حلقه live را مشابه paper بنویس ولی به جای")
    print("PaperBroker از NobitexBroker استفاده کن. (سند طراحی، فصل ۵ را ببین)")


# ==============================================================
def run_serve_mode(port=8000):
    """سرور + اپلیکیشن موبایل — سیگنال واقعی با درجه اعتبارسنجی"""
    from server.app import run as run_server
    run_server(port)


# ==============================================================
def main():
    parser = argparse.ArgumentParser(description="ربات سرخط‌زن — معاملات الگوریتمی چند نوبتی")
    parser.add_argument("mode", choices=["backtest", "paper", "serve", "live", "brain"],
                        help="backtest = تست تاریخی | paper = شبیه‌سازی | serve = اپ موبایل + سیگنال | live = معامله واقعی")
    parser.add_argument("--market", choices=["tse", "crypto", "both"], default="tse",
                        help="(فقط برای backtest) کدام بازار تست شود")
    parser.add_argument("--days", type=int, default=90, help="(فقط برای backtest) تعداد روز تاریخی")
    parser.add_argument("--capital", type=float, default=None, help="سرمایه اولیه (پیش‌فرض از config.py)")
    parser.add_argument("--port", type=int, default=8000, help="(فقط برای serve) پورت سرور")
    args = parser.parse_args()

    if args.mode == "backtest":
        run_backtest_mode(market=args.market, days=args.days, capital=args.capital)
    elif args.mode == "paper":
        run_paper_mode()
    elif args.mode == "serve":
        run_serve_mode(port=args.port)
    elif args.mode == "live":
        run_live_mode()
    elif args.mode == "brain":
        from cloud.brain import main as brain_main
        sys.exit(brain_main([]))   # خروجی در webapp/data نوشته می‌شود


if __name__ == "__main__":
    main()
