# -*- coding: utf-8 -*-
"""
اجرای بک‌تست از خط فرمان
===========================
مثال‌ها:
  python backtest/run_backtest.py                     # بورس با داده نمونه
  python backtest/run_backtest.py --market crypto     # کریپتو با داده نمونه
  python backtest/run_backtest.py --market both       # هر دو بازار
  python backtest/run_backtest.py --capital 50000000  # با سرمایه ۵۰ میلیون

اگر اینترنت ایران داری و کتابخانه pytse-client نصب است، می‌توانی
دیتای واقعی هم بدهی (به README بخش «بک‌تست با دیتای واقعی» نگاه کن).
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtest.sample_data import generate_sample_15m
from backtest.engine import run_backtest, print_stats
import config


def main():
    parser = argparse.ArgumentParser(description="بک‌تست ربات سرخط‌زن")
    parser.add_argument("--market", choices=["tse", "crypto", "both"],
                        default="tse", help="کدام بازار تست شود")
    parser.add_argument("--days", type=int, default=90, help="تعداد روز تاریخی")
    parser.add_argument("--capital", type=float, default=None,
                        help="سرمایه اولیه (پیش‌فرض از config.py)")
    parser.add_argument("--seed", type=int, default=42, help="seed داده نمونه")
    args = parser.parse_args()

    markets = ["tse", "crypto"] if args.market == "both" else [args.market]
    for market in markets:
        print(f"\n⏳ در حال اجرای بک‌تست برای بازار: "
              f"{'بورس تهران' if market == 'tse' else 'کریپتو'} ...")
        df = generate_sample_15m(
            days=args.days,
            seed=args.seed,
            market=market,
            start_price=100_000 if market == "tse" else 500_000,
        )
        stats, chart, csv_path = run_backtest(df, market=market,
                                              capital=args.capital)
        print_stats(stats)
        if chart:
            print(f"  📊 نمودار: {chart}")
        if csv_path:
            print(f"  📁 لاگ معاملات: {csv_path}")


if __name__ == "__main__":
    main()
