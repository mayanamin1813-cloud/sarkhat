/* ============================================================
 * سرخط‌زن — موتور بیت‌کوین ۵ دقیقه‌ای روی گوشی (هم‌راستا با مغز ابری)
 * Watcher → Brain → Executor → Guard
 * منبع دیتا: نوبیتکس (داخل ایران) → Coinbase → CoinGecko
 * شرط‌بندی شبیه‌سازی (پیپر) در localStorage — هیچ پول واقعی درگیر نیست
 * ============================================================ */
"use strict";

window.BTC = (function () {
  const WIN = 300;              // پنجره ۵ دقیقه‌ای
  const MIN_CONF = 60;          // زیر این: صبر
  const MIN_CONF_BET = 62;      // حداقل اعتماد برای شرط شبیه‌سازی
  const PAYOUT = 0.85;          // سود شرط درست
  const MAX_BETS_DAY = 15;
  const LOSS_CAP_PCT = 0.08;
  const STAKE_PCT = 0.01;
  const COOLDOWN_SEC = 15 * 60;

  const $id = (id) => document.getElementById(id);
  const fmt = (n, d = 0) => (n === undefined || n === null || isNaN(n))
    ? "—" : Number(n).toLocaleString("fa-IR", { maximumFractionDigits: d });

  const S = {
    candles: null, source: "—", unit: "USD",
    signal: null, lastError: "",
    autoBet: localStorage.getItem("sarkhat_autobet") === "1",
    book: null, timer: null, busy: false,
  };

  // ---------- ریاضی (دقیقاً مثل cloud/brain.py) ----------
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

  function ema(values, period) {
    if (values.length < period) return null;
    const k = 2 / (period + 1);
    let e = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
    for (let i = period; i < values.length; i++) e = values[i] * k + e * (1 - k);
    return e;
  }

  function rsi(closes, period = 14) {
    if (closes.length < period + 1) return 50;
    let g = 0, l = 0;
    for (let i = 1; i <= period; i++) {
      const d = closes[i] - closes[i - 1];
      if (d > 0) g += d; else l -= d;
    }
    let ag = g / period, al = l / period;
    for (let i = period + 1; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1];
      ag = (ag * (period - 1) + Math.max(d, 0)) / period;
      al = (al * (period - 1) + Math.max(-d, 0)) / period;
    }
    if (al === 0) return 100;
    return 100 - 100 / (1 + ag / al);
  }

  function compute(candles) {
    if (!candles || candles.length < 30) return null;
    const closes = candles.map((c) => c.close);
    const n = closes.length;
    const rets = [];
    for (let i = 1; i < n; i++) rets.push((closes[i] - closes[i - 1]) / closes[i - 1]);

    const mom1 = rets[rets.length - 1];
    const mom3 = rets.slice(-3).reduce((a, b) => a + b, 0);
    const e9 = ema(closes, 9), e21 = ema(closes, 21);
    const emaGap = (e9 && e21) ? (e9 - e21) / e21 : 0;
    const r = rsi(closes, 14);
    const base = rets.slice(-20).reduce((a, b) => a + Math.abs(b), 0) / 20 || 1e-9;
    const now = rets.slice(-6).reduce((a, b) => a + Math.abs(b), 0) / 6;
    const volRegime = now / base;
    const sma20 = closes.slice(-20).reduce((a, b) => a + b, 0) / 20;
    const var20 = closes.slice(-20).reduce((a, b) => a + (b - sma20) ** 2, 0) / 20;
    const sd20 = Math.sqrt(var20) || 1e-9;
    const bbPos = clamp((closes[n - 1] - sma20) / (2 * sd20), -1.5, 1.5);

    const s = {
      mom1: clamp(mom1 / 0.003, -1, 1),
      mom3: clamp(mom3 / 0.006, -1, 1),
      ema: clamp(emaGap / 0.0015, -1, 1),
      rsi: clamp((r - 50) / 25, -1, 1),
      bb: -clamp(bbPos / 1.2, -1, 1),
    };
    let score = 0.22 * s.mom1 + 0.20 * s.mom3 + 0.22 * s.ema + 0.16 * s.rsi + 0.20 * s.bb;
    score = clamp(score, -1, 1);

    let confidence = Math.round(50 + Math.abs(score) * 45);
    let note = "";
    if (volRegime > 2.5) { confidence = Math.round(confidence * 0.9); note = "نوسان غیرعادی — اعتماد کم شد"; }
    const direction = score >= 0 ? "UP" : "DOWN";
    let action = direction;
    if (Math.abs(score) < 0.12 || confidence < MIN_CONF) {
      action = "WAIT";
      if (!note) note = "سیگنال ضعیف — صبر";
    }
    return {
      direction, action, confidence, note,
      features: {
        mom1_pct: +(mom1 * 100).toFixed(3), mom3_pct: +(mom3 * 100).toFixed(3),
        ema_gap_pct: +(emaGap * 100).toFixed(3), rsi: +r.toFixed(1),
        vol_regime: +volRegime.toFixed(2), bb_pos: +bbPos.toFixed(2),
      },
    };
  }

  // ---------- WATCHER: منابع دیتا ----------
  async function jget(url, timeout = 12000) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeout);
    try {
      const r = await fetch(url, { signal: ctrl.signal });
      if (!r.ok) throw new Error("http " + r.status);
      return await r.json();
    } finally { clearTimeout(t); }
  }

  async function fromNobitex() {
    const to = Math.floor(Date.now() / 1000), from = to - 86400;
    const d = await jget(`https://api.nobitex.ir/market/udf/history?symbol=BTCIRT&resolution=5&from=${from}&to=${to}`);
    if (d.s !== "ok" || !d.t || !d.t.length) throw new Error("bad nobitex");
    const now = Math.floor(Date.now() / 1000);
    const candles = d.t.map((ts, i) => ({
      time: +ts, open: +d.o[i], high: +d.h[i], low: +d.l[i], close: +d.c[i], volume: +d.v[i] || 0,
    })).filter((c) => c.time + WIN <= now);
    if (candles.length < 40) throw new Error("few candles");
    return { candles: candles.slice(-240), source: "نوبیتکس", unit: "IRT" };
  }

  async function fromCoinbase() {
    const rows = await jget("https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=300");
    const now = Math.floor(Date.now() / 1000);
    const candles = rows.map((r) => ({
      time: +r[0], low: +r[1], high: +r[2], open: +r[3], close: +r[4], volume: +r[5],
    })).filter((c) => c.time + WIN <= now).sort((a, b) => a.time - b.time);
    if (candles.length < 40) throw new Error("few candles");
    return { candles: candles.slice(-240), source: "Coinbase", unit: "USD" };
  }

  async function fromCoinGecko() {
    const d = await jget("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=1");
    const buckets = {};
    (d.prices || []).forEach(([ts, px]) => {
      const b = Math.floor(ts / WIN) * WIN;
      (buckets[b] = buckets[b] || []).push(+px);
    });
    const now = Math.floor(Date.now() / 1000);
    const candles = Object.keys(buckets).map(Number).sort((a, b) => a - b).map((b) => {
      const p = buckets[b];
      return { time: b, open: p[0], high: Math.max(...p), low: Math.min(...p), close: p[p.length - 1], volume: 0 };
    }).filter((c) => c.time + WIN <= now);
    if (candles.length < 40) throw new Error("few candles");
    return { candles: candles.slice(-240), source: "CoinGecko", unit: "USD" };
  }

  async function refreshCandles() {
    const chain = [fromNobitex, fromCoinbase, fromCoinGecko];
    let lastErr = null;
    for (const f of chain) {
      try { const r = await f(); return r; } catch (e) { lastErr = e; }
    }
    throw lastErr || new Error("no source");
  }

  // ---------- EXECUTOR + GUARD (دفتر شبیه‌سازی روی گوشی) ----------
  function newBook() {
    return { start_equity: 1000, equity: 1000, open: null, bets: [],
             day: { date: "", trades: 0, wins: 0, pnl: 0 }, loss_streak: 0, cooldown_until: 0 };
  }
  const tehranDay = () => new Date(Date.now() + 3.5 * 3600 * 1000)
    .toISOString().slice(0, 10);

  function loadBook() {
    try { S.book = JSON.parse(localStorage.getItem("sarkhat_btc_book")) || newBook(); }
    catch (e) { S.book = newBook(); }
    if (S.book.day.date !== tehranDay()) S.book.day = { date: tehranDay(), trades: 0, wins: 0, pnl: 0 };
  }
  const saveBook = () => localStorage.setItem("sarkhat_btc_book", JSON.stringify(S.book));

  function guardAllow(sig) {
    const b = S.book;
    if (b.open) return [false, "پنجره باز داریم"];
    if (sig.action === "WAIT" || sig.confidence < MIN_CONF_BET) return [false, "اعتماد کم"];
    if (b.day.trades >= MAX_BETS_DAY) return [false, "سقف روزانه پر شد"];
    if (b.day.pnl <= -b.start_equity * LOSS_CAP_PCT) return [false, "سقف ضرر روز"];
    if (Date.now() / 1000 < b.cooldown_until) return [false, "استراحت بعد از باخت‌ها"];
    return [true, ""];
  }

  function settle() {
    const b = S.book, bet = b.open;
    if (!bet || !S.candles) return null;
    const match = S.candles.find((c) => c.time === bet.window_start);
    if (!match) return null;
    let result, pnl;
    if (match.close === bet.entry) { result = "push"; pnl = 0; }
    else {
      const up = match.close > bet.entry;
      const win = bet.dir === "UP" ? up : !up;
      result = win ? "win" : "loss";
      pnl = win ? bet.stake * PAYOUT : -bet.stake;
    }
    b.equity = +(b.equity + pnl).toFixed(2);
    b.day.pnl = +(b.day.pnl + pnl).toFixed(2);
    if (result === "win" || result === "push") b.loss_streak = 0;
    else {
      b.loss_streak++;
      if (b.loss_streak >= 3) { b.cooldown_until = Date.now() / 1000 + COOLDOWN_SEC; b.loss_streak = 0; }
    }
    if (result === "win") b.day.wins++;
    bet.end_price = match.close; bet.result = result; bet.pnl = +pnl.toFixed(2);
    b.bets.push(bet); b.bets = b.bets.slice(-60); b.open = null;
    saveBook();
    return { result, pnl };
  }

  function maybeOpen() {
    if (!S.signal || !S.candles || !S.autoBet) return;
    const now = Math.floor(Date.now() / 1000);
    const t0 = Math.floor(now / WIN) * WIN;
    if (now - t0 > 60) return;
    const [ok] = guardAllow(S.signal);
    if (!ok) return;
    const prev = S.candles.find((c) => c.time === t0 - WIN);
    if (!prev) return;
    const b = S.book;
    b.open = {
      window_start: t0, dir: S.signal.direction, entry: prev.close,
      stake: +(b.equity * STAKE_PCT).toFixed(2), confidence: S.signal.confidence,
    };
    b.day.trades++;
    saveBook();
  }

  // ---------- رندر ----------
  const DIR_FA = { UP: "▲ صعودی", DOWN: "▼ نزولی", WAIT: "… صبر" };
  const dirClass = (a) => a === "UP" ? "dir-up" : a === "DOWN" ? "dir-down" : "dir-wait";

  function render() {
    const sig = S.signal;
    const el = $id("btcDir");
    if (!el) return;
    if (!sig) {
      el.innerHTML = `<span class="dir-wait">دیتا در دسترس نیست</span>`;
      if ($id("btcPrice")) $id("btcPrice").textContent = "—";
      const err = $id("btcNote"); if (err && S.lastError) err.textContent = "اتصال به صرافی‌ها ناموفق بود — دوباره تلاش می‌کنم";
      return;
    }
    el.innerHTML = `<span class="${dirClass(sig.action)}">${DIR_FA[sig.action]}</span>`;
    $id("btcConf").textContent = fmt(sig.confidence) + "٪";
    const bar = $id("btcConfBar");
    if (bar) { bar.style.width = sig.confidence + "%"; bar.className = "conf-fill " + dirClass(sig.action); }
    const last = S.candles[S.candles.length - 1];
    $id("btcPrice").textContent = fmt(last.close, S.unit === "IRT" ? 0 : 2) + " " + (S.unit === "IRT" ? "تومان" : "$");
    $id("btcSrc").textContent = "منبع: " + S.source + (S.lastError ? " • منبع اصلی در دسترس نبود" : "");
    $id("btcNote").textContent = sig.note || "";
    const cd = $id("btcCountdown");
    if (cd) {
      const end = (Math.floor(Date.now() / 1000 / WIN) * WIN) + WIN;
      cd.textContent = fmt(Math.max(0, Math.floor((end - Date.now() / 1000) / 60))) + ":" +
        String(Math.max(0, Math.floor((end - Date.now() / 1000) % 60))).padStart(2, "0");
    }
    const f = sig.features, F = $id("btcFeat");
    if (F) F.innerHTML = [
      ["مومنتوم آخرین کندل", (f.mom1_pct > 0 ? "+" : "") + f.mom1_pct + "٪"],
      ["مومنتوم ۳ کندل", (f.mom3_pct > 0 ? "+" : "") + f.mom3_pct + "٪"],
      ["اختلاف EMA ۹/۲۱", (f.ema_gap_pct > 0 ? "+" : "") + f.ema_gap_pct + "٪"],
      ["RSI", fmt(f.rsi, 1)],
      ["رژیم نوسان", fmt(f.vol_regime, 2) + "×"],
      ["موقعیت باند", fmt(f.bb_pos, 2)],
    ].map(([k, v]) => `<div class="feat"><span>${k}</span><b dir="ltr">${v}</b></div>`).join("");

    // دفتر شبیه‌سازی گوشی
    const b = S.book;
    $id("btcEquity").textContent = fmt(b.equity, 1);
    $id("btcToday").textContent = `${fmt(b.day.trades)} شرط • ${fmt(b.day.wins)} برد • سود/زیان: ${fmt(b.day.pnl, 1)}`;
    const openEl = $id("btcOpenBet");
    if (b.open) {
      openEl.className = "open-bet live";
      openEl.innerHTML = `🎯 شرط باز: <b class="${dirClass(b.open.dir)}">${DIR_FA[b.open.dir]}</b> • ورود <b dir="ltr">${fmt(b.open.entry, 2)}</b> • مبلغ ${fmt(b.open.stake, 1)} • اعتماد ${fmt(b.open.confidence)}٪`;
    } else {
      openEl.className = "open-bet";
      openEl.textContent = "شرط باز نداریم";
    }
    const list = $id("btcBets");
    list.innerHTML = b.bets.slice().reverse().slice(0, 20).map((x) => {
      const ic = x.result === "win" ? "✅" : x.result === "loss" ? "❌" : "➖";
      return `<div class="bet-row"><span>${ic} ${DIR_FA[x.dir]}</span>
        <span dir="ltr">${fmt(x.entry, 2)} → ${fmt(x.end_price, 2)}</span>
        <span class="${x.pnl > 0 ? "pnl-pos" : x.pnl < 0 ? "pnl-neg" : ""}" dir="ltr">${x.pnl > 0 ? "+" : ""}${fmt(x.pnl, 2)}</span></div>`;
    }).join("") || `<div class="empty">هنوز شرطی ثبت نشده — «شرط خودکار» را روشن کن یا صبر کن سیگنال قوی بیاید.</div>`;
    const ab = $id("autoBet");
    if (ab) ab.checked = S.autoBet;
  }

  function renderCloudPaper(live) {
    const el = $id("btcCloudPaper");
    if (!el || !live || !live.btc_paper) return;
    const p = live.btc_paper;
    const st = p.stats || {};
    el.innerHTML = `<div class="cloud-paper">
      <div class="kv"><span>ربات ابری (مرجع)</span><b>${fmt(p.equity, 1)} واحد</b></div>
      <div class="kv"><span>برد/باخت کل</span><b>${fmt(st.wins)} / ${fmt(st.losses)}</b></div>
      <div class="kv"><span>نرخ برد</span><b>${st.win_rate === null || st.win_rate === undefined ? "—" : fmt(st.win_rate, 1) + "٪"}</b></div>
      <div class="kv"><span>امروز</span><b>${fmt(p.today?.trades || 0)} شرط • ${fmt(p.today?.pnl || 0, 1)}</b></div>
    </div>`;
  }

  // ---------- حلقه ----------
  async function tick() {
    if (S.busy) return;
    S.busy = true;
    try {
      const r = await refreshCandles();
      S.candles = r.candles; S.source = r.source; S.unit = r.unit; S.lastError = "";
      S.signal = compute(S.candles);
      settle();
      maybeOpen();
    } catch (e) {
      S.lastError = String(e && e.message || e);
    }
    render();
    S.busy = false;
  }

  function init() {
    loadBook();
    const ab = $id("autoBet");
    if (ab) ab.addEventListener("change", () => {
      S.autoBet = ab.checked;
      localStorage.setItem("sarkhat_autobet", S.autoBet ? "1" : "0");
      render();
    });
    tick();
    S.timer = setInterval(tick, 30000);   // هر ۳۰ ثانیه — سبک برای اینترنت و باتری
    return S;
  }

  return {
    init, tick, render, renderCloudPaper,
    get state() { return S; },
    compute,
  };
})();
