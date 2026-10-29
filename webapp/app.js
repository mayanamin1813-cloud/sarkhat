/* سرخط‌زن — منطق اپلیکیشن (جاوااسکریپت خالص، بدون فریمورک) */
"use strict";

// ---------- وضعیت ----------
const $ = (sel) => document.querySelector(sel);
const state = {
  serverUrl: localStorage.getItem("sarkhat_server") || "",
  filter: "all",
  signals: { tse: [], crypto: [] },
  market: null,
  trades: null,
  settings: null,
  demo: false,
  online: false,
  cloud: false,          // حالت ابری: دیتا از data/live.json (GitHub Pages) می‌آید — بدون سرور!
  live: null,
  refreshTimer: null,
};

const DEFAULT_URL = () => (state.serverUrl || location.origin);
const api = (path) => DEFAULT_URL().replace(/\/$/, "") + path;

const fmt = (n, d = 0) => {
  if (n === undefined || n === null || isNaN(n)) return "—";
  return Number(n).toLocaleString("fa-IR", { maximumFractionDigits: d });
};
const GRADE_CLASS = { "A": "grade-A", "B": "grade-B", "C": "grade-C", "-": "grade-D" };
const GRADE_FA = { "A": "عالی", "B": "خوب", "C": "ضعیف", "-": "رد" };

// ---------- تب‌ها ----------
document.querySelectorAll(".tabbtn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tabbtn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    btn.classList.add("active");
    $("#tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "market") loadMarket();
    if (btn.dataset.tab === "trades") loadTrades();
    if (btn.dataset.tab === "settings") fillSettings();
    if (btn.dataset.tab === "btc" && window.BTC) BTC.tick();
  });
});

document.querySelectorAll(".chip[data-mkt]").forEach((chip) => {
  chip.addEventListener("click", () => {
    document.querySelectorAll(".chip[data-mkt]").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    state.filter = chip.dataset.mkt;
    renderSignals();
  });
});

$("#scanNow").addEventListener("click", async () => {
  const btn = $("#scanNow");
  btn.textContent = "… در حال بروزرسانی";
  if (state.cloud) {
    await loadCloud();
    if (window.BTC) BTC.tick();
    btn.textContent = "⟳ بروزرسانی";
    return;
  }
  try {
    const r = await fetch(api("/api/scan"), { method: "POST" });
    const data = await r.json();
    if (data.signals) {
      state.signals = data.signals;
      renderSignals();
    }
  } catch (e) { setConn(false); }
  btn.textContent = "⟳ اسکن فوری";
});

// ---------- اتصال ----------
function setConn(ok, msg) {
  state.online = ok;
  const el = $("#connStatus");
  el.classList.remove("ok", "err");
  if (ok) { el.classList.add("ok"); el.textContent = msg || "متصل"; }
  else { el.classList.add("err"); el.textContent = msg || "قطع — سرور در دسترس نیست"; }
}

async function loadHealth() {
  try {
    const r = await fetch(api("/api/health"), { signal: AbortSignal.timeout ? AbortSignal.timeout(6000) : undefined });
    const h = await r.json();
    state.settings = h.settings;
    state.demo = !!h.demo;
    state.cloud = false;
    $("#demoBanner").classList.toggle("hidden", !h.demo);
    $("#tehranClock").textContent = (h.tehran_time || "").split(" ")[1] || "--:--";
    const parts = [];
    if (h.tse_market_open) parts.push("بورس باز");
    else parts.push("بورس بسته");
    parts.push(h.demo ? "دیتای نمایشی" : (h.crypto_reachable || h.tse_reachable ? "دیتای واقعی" : "دیتا در دسترس نیست"));
    setConn(true, parts.join(" • "));
    return true;
  } catch (e) {
    // سرور محلی نیست؟ حالت ابری را امتحان کن (GitHub Pages — بدون کامپیوتر!)
    const cloudOk = await loadCloud();
    if (cloudOk) return true;
    $("#demoBanner").classList.add("hidden");
    setConn(false, "نه سرور وصل است نه دیتای ابری — اینترنت را چک کن");
    return false;
  }
}

// ---------- حالت ابری (بدون سرور — دیتای زنده از گیت‌هاب) ----------
async function loadCloud() {
  try {
    const r = await fetch("data/live.json?ts=" + Date.now(), { signal: AbortSignal.timeout ? AbortSignal.timeout(8000) : undefined });
    if (!r.ok) throw new Error("http " + r.status);
    const live = await r.json();
    state.cloud = true;
    state.live = live;
    state.signals = live.signals || { tse: [], crypto: [] };
    state.demo = !!live.demo;
    $("#demoBanner").classList.add("hidden");
    $("#tehranClock").textContent = (live.tehran_time || "").split(" ")[1] || "--:--";
    const ageMin = live.updated_at ? Math.round((Date.now() - Date.parse(live.updated_at.replace(" ", "T") + "+03:30")) / 60000) : null;
    const brain = live.brain || {};
    const src = brain.btc_source ? " • BTC: " + brain.btc_source : "";
    setConn(true, "حالت ابری ✓" + (live.tse_open ? " • بورس باز" : " • بورس بسته") +
      (ageMin !== null && !isNaN(ageMin) ? ` • دیتا: ${fmt(ageMin)} دقیقه پیش` : "") + src);
    renderSignals();
    if (window.BTC && live.btc_paper) BTC.renderCloudPaper(live);
    scheduleRefresh();
    return true;
  } catch (e) {
    return false;
  }
}

// ---------- سیگنال‌ها ----------
async function loadSignals() {
  if (state.cloud) { renderSignals(); return; }
  try {
    const r = await fetch(api("/api/signals"));
    const data = await r.json();
    state.signals = data.signals || { tse: [], crypto: [] };
    state.demo = !!data.demo;
    $("#tehranClock").textContent = (data.time || "").split(" ")[1] || "--:--";
    $("#demoBanner").classList.toggle("hidden", !state.demo);
    setConn(true, data.tse_open ? "متصل • بورس باز" : "متصل");
    renderSignals();
  } catch (e) { setConn(false); }
}

function signalCard(s) {
  const valid = !s.rejected && s.grade !== "-" && s.score >= 50;
  const cls = valid ? GRADE_CLASS[s.grade] : "grade--";
  const gradeTxt = valid ? s.grade : "×";
  const factorsHtml = (s.factors || []).map((f) => `
    <div class="factor ${f.passed ? "ok" : "no"}">
      <div>
        <div class="t"><span class="ic">${f.passed ? "✓" : "✗"}</span>${f.title}</div>
        <div class="d">${f.detail || ""}</div>
      </div>
      <div class="pts">${f.points}/${f.max_points}</div>
    </div>`).join("");
  return `
  <div class="card" data-sym="${s.symbol}" data-mkt="${s.market}">
    <div class="sig-head">
      <div class="sig-title">${s.symbol} <span class="mkt">${s.market === "tse" ? "بورس" : "کریپتو"}</span></div>
      <div class="grade ${cls}">${gradeTxt}</div>
    </div>
    <div class="score-line">
      امتیاز اعتبارسنجی: <b>${fmt(s.score)}</b> از ۱۰۰
      ${valid ? `— درجه <b>${s.grade} (${GRADE_FA[s.grade]})</b>` : ""}
      ${s.rsi ? ` • RSI: ${fmt(s.rsi)}` : ""}
      ${s.volume_ratio ? ` • حجم: ${fmt(s.volume_ratio, 1)}×` : ""}
    </div>
    ${s.rejected ? `<div class="reject">✗ ${s.reject_reason || "رد شد"}</div>` : ""}
    ${s.entry ? `
    <div class="prices">
      <div class="p-box p-stop"><div class="lbl">حد ضرر</div><div class="val">${fmt(s.stop)}</div></div>
      <div class="p-box p-entry"><div class="lbl">ورود</div><div class="val">${fmt(s.entry)}</div></div>
      <div class="p-box p-target"><div class="lbl">حد سود</div><div class="val">${fmt(s.target)}</div></div>
    </div>
    <button class="check-toggle" type="button">چک‌لیست ۸ فاکتوری اعتبارسنجی ▾</button>
    <div class="factors">${factorsHtml}</div>` : ""}
  </div>`;
}

function renderSignals() {
  const all = [...state.signals.crypto, ...state.signals.tse];
  let list = all;
  if (state.filter === "crypto") list = state.signals.crypto;
  else if (state.filter === "tse") list = state.signals.tse;
  else if (state.filter === "valid") list = all.filter((s) => !s.rejected && s.grade !== "-");
  const box = $("#signalsList");
  if (!list.length) {
    const hint = state.cloud
      ? `مغز ابری هنوز سیگنالی صادر نکرده.<br>ربات ابری هر ۵ دقیقه بازار را اسکن می‌کند؛ چند دقیقه دیگر صفحه را بروزرسانی کن.<br>${state.live?.tse_open === false ? "بورس تهران الان بسته است — سیگنال بورس در ساعات بازار (۹ تا ۱۲:۴۵) می‌آید." : ""}`
      : `هنوز سیگنالی نیست.<br>ربات هر ${fmt(state.settings?.scan_interval_sec || 90)} ثانیه بازار را اسکن می‌کند؛ صبر کن یا «اسکن فوری» بزن.`;
    box.innerHTML = `<div class="empty">${hint}</div>`;
    return;
  }
  box.innerHTML = list.map(signalCard).join("");
  box.querySelectorAll(".check-toggle").forEach((btn) => {
    btn.addEventListener("click", () => btn.nextElementSibling.classList.toggle("open"));
  });
}

// ---------- بازار ----------
async function loadMarket() {
  if (state.cloud) { renderMarketCloud(); return; }
  try {
    const r = await fetch(api("/api/market"));
    state.market = await r.json();
    renderMarket();
  } catch (e) { setConn(false); }
}

function renderMarketCloud() {
  const live = state.live;
  if (!live) return;
  const rows = [];
  // بیت‌کوین زنده از موتور گوشی (نوبیتکس/Coinbase)
  const bs = window.BTC ? BTC.state : null;
  if (bs && bs.candles && bs.candles.length) {
    const last = bs.candles[bs.candles.length - 1];
    const prev = bs.candles[bs.candles.length - 2] || last;
    rows.push({ symbol: "BTCIRT", last: last.close, dayChange: (last.close / prev.close - 1) * 100, source: bs.source });
  }
  const btc = live.btc || {};
  if (btc.price_irt) rows.unshift({ symbol: "BTCIRT (تومانی)", last: btc.price_irt, dayChange: 0, source: "نوبیتکس" });
  $("#marketCrypto").innerHTML = rows.map(marketRow).join("") || `<div class="empty">در حال گرفتن قیمت…</div>`;
  // بورس از دیتای زنده مغز ابری (آخرین اسکن ساعات بازار)
  const tse = (live.signals?.tse || []).filter((s) => s.last);
  $("#marketTse").innerHTML = tse.length
    ? tse.map((s) => marketRow({ symbol: s.symbol, last: s.last, dayChange: s.day_change_pct || 0, source: "TSETMC (آخرین اسکن ابری)" })).join("")
    : `<div class="empty">در ساعات بازار بورس (۹ تا ۱۲:۴۵) قیمت‌ها اینجا می‌آید.</div>`;
  const badge = $("#tseOpenBadge");
  badge.textContent = live.tse_open ? "باز" : "بسته";
  badge.className = "badge " + (live.tse_open ? "badge-green" : "badge-red");
}

function marketRow(m) {
  const chg = m.dayChange;
  const chgCls = chg >= 0 ? "up" : "down";
  return `
  <div class="mrow">
    <div>
      <div class="sym">${m.symbol}</div>
      <div class="src">منبع: ${m.source || "—"} ${m.error ? "• " + m.error : ""}</div>
    </div>
    <div class="px">
      <div class="last">${fmt(m.last)}</div>
      <div class="chg ${chgCls}">${chg >= 0 ? "▲" : "▼"} ${fmt(Math.abs(chg), 2)}٪</div>
    </div>
  </div>`;
}

function renderMarket() {
  const m = state.market;
  if (!m) return;
  $("#marketCrypto").innerHTML = m.crypto.map(marketRow).join("") || `<div class="empty">—</div>`;
  $("#marketTse").innerHTML = m.tse.map(marketRow).join("") || `<div class="empty">—</div>`;
  const badge = $("#tseOpenBadge");
  badge.textContent = m.tse_open ? "باز" : "بسته";
  badge.className = "badge " + (m.tse_open ? "badge-green" : "badge-red");
}

// ---------- معاملات ----------
async function loadTrades() {
  if (state.cloud) { renderTradesCloud(); return; }
  try {
    const r = await fetch(api("/api/trades"));
    state.trades = await r.json();
    renderTrades();
  } catch (e) { setConn(false); }
}

function renderTradesCloud() {
  const live = state.live;
  if (!live) return;
  const p = live.btc_paper || {};
  const st = p.stats || {};
  const tseOpen = live.tse_open;
  $("riskStrip").innerHTML = `
    <div class="risk-box">
      <h4>ربات ابری — بیت‌کوین ۵ دقیقه‌ای (شبیه‌سازی)</h4>
      <div class="kv"><span>موجودی شبیه‌سازی</span><b>${fmt(p.equity)} واحد</b></div>
      <div class="kv"><span>معاملات امروز</span><b>${fmt(p.today?.trades || 0)} / ۱۵</b></div>
      <div class="kv"><span>سود/زیان امروز</span><b>${fmt(p.today?.pnl || 0, 1)}</b></div>
      <div class="kv"><span>نرخ برد کل</span><b>${st.win_rate === null || st.win_rate === undefined ? "—" : fmt(st.win_rate, 1) + "٪"}</b></div>
      <div class="kv"><span>وضعیت</span><b>فعال (هر ۵ دقیقه در ابر)</b></div>
    </div>
    <div class="risk-box">
      <h4>بورس تهران</h4>
      <div class="kv"><span>وضعیت بازار</span><b>${tseOpen ? "باز" : "بسته"}</b></div>
      <div class="kv"><span>سیگنال‌های جلسه</span><b>${fmt((live.signals?.tse || []).filter((s) => !s.rejected && s.grade !== "-").length)}</b></div>
    </div>`;
  const bets = (p.bets || []).slice().reverse();
  $("tradesList").innerHTML = bets.length
    ? `<div class="card">${bets.map(cloudBetRow).join("")}</div>`
    : `<div class="empty">ربات ابری هنوز شرطی ثبت نکرده — وقتی اعتماد از ۶۲٪ بالاتر برود خودش ثبت می‌کند.</div>`;
}

function cloudBetRow(t) {
  const ic = t.result === "win" ? "✅" : t.result === "loss" ? "❌" : "➖";
  const dir = t.dir === "UP" ? "صعودی" : "نزولی";
  return `
  <div class="trade">
    <div>
      <div class="${t.dir === "UP" ? "side-buy" : "side-sell"}">${ic} ${dir} بیت‌کوین</div>
      <div class="tinfo">پنجره ۵ دقیقه • اعتماد ${fmt(t.confidence)}٪ • ${t.closed_at || ""}</div>
    </div>
    <div>
      <div style="direction:ltr">${fmt(t.entry, 2)} → ${fmt(t.end_price, 2)}</div>
      <div class="tinfo ${t.pnl > 0 ? "pnl-pos" : t.pnl < 0 ? "pnl-neg" : ""}" style="direction:ltr">PnL: ${t.pnl > 0 ? "+" : ""}${fmt(t.pnl, 2)}</div>
    </div>
  </div>`;
}

function riskBox(name, d) {
  if (!d) return "";
  return `
  <div class="risk-box">
    <h4>${name}</h4>
    <div class="kv"><span>معاملات امروز</span><b>${fmt(d.risk.trades_today)} / ${fmt(d.risk.max_trades)}</b></div>
    <div class="kv"><span>سود/زیان امروز</span><b>${fmt(d.risk.pnl_today)}</b></div>
    <div class="kv"><span>ارزش حساب</span><b>${fmt(d.equity)}</b></div>
    <div class="kv"><span>وضعیت</span><b class="${d.risk.halted ? "halted" : ""}">${d.risk.halted ? "متوقف (سقف ضرر)" : "فعال"}</b></div>
  </div>`;
}

function tradeRow(t) {
  const isBuy = t.side === "خرید";
  return `
  <div class="trade">
    <div>
      <div class="${isBuy ? "side-buy" : "side-sell"}">${t.side} ${fmt(t.qty)}</div>
      <div class="tinfo">${t.time || ""} • ${t.reason || ""}</div>
    </div>
    <div>
      <div class="${isBuy ? "" : t.pnl > 0 ? "pnl-pos" : "pnl-neg"}" style="direction:ltr">${fmt(t.price)}</div>
      ${!isBuy ? `<div class="tinfo" style="direction:ltr">PnL: ${fmt(t.pnl)}</div>` : ""}
    </div>
  </div>`;
}

function renderTrades() {
  const t = state.trades;
  if (!t) return;
  $("#riskStrip").innerHTML = riskBox("کریپتو", t.crypto) + riskBox("بورس تهران", t.tse);
  const rows = [...(t.crypto?.trades || []), ...(t.tse?.trades || [])].reverse();
  $("#tradesList").innerHTML = rows.length
    ? `<div class="card">${rows.map(tradeRow).join("")}</div>`
    : `<div class="empty">هنوز معامله شبیه‌سازی ثبت نشده.<br>وقتی سیگنال معتبر (درجه کافی) بیاید، ربات خودش ثبت می‌کند.</div>`;
}

// ---------- تنظیمات ----------
function fillSettings() {
  const s = state.settings;
  $("#serverUrl").value = state.serverUrl || "";
  if (s) {
    $("#autoPaper").checked = !!s.auto_paper;
    $("#lowData").checked = !!s.low_data;
    $("#scanInterval").value = s.scan_interval_sec || 90;
    document.querySelectorAll("#gradePicker .chip").forEach((c) => {
      c.classList.toggle("active", c.dataset.grade === s.min_grade);
    });
  }
  if (state.cloud) {
    const note = $("#serverModeNote");
    if (note) note.textContent = "حالا در حالت ابری هستی — دیتای زنده از گیت‌هاب می‌آید و به هیچ سروری نیاز نیست. اگر ربات را با Termux بالا آوردی، آدرس بده تا مستقیم به آن وصل شوم.";
  }
  const tgToken = $("#tgToken"), tgChat = $("#tgChat");
  if (tgToken) tgToken.value = localStorage.getItem("sarkhat_tg_token") || "";
  if (tgChat) tgChat.value = localStorage.getItem("sarkhat_tg_chat") || "";
  const ab = $("#autoBet");
  if (ab && window.BTC) ab.checked = BTC.state.autoBet;
}

function clientTelegram(text) {
  const token = (localStorage.getItem("sarkhat_tg_token") || "").trim();
  const chat = (localStorage.getItem("sarkhat_tg_chat") || "").trim();
  if (!token || !chat) return;
  fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chat, text }),
  }).catch(() => {});
}

// اعلان سیگنال معتبر تازه به تلگرام (اگر در تنظیمات ذخیره کرده باشی)
let lastNotifiedKey = "";
function maybeNotifySignals() {
  const valid = [...state.signals.tse, ...state.signals.crypto]
    .filter((s) => !s.rejected && (s.grade === "A" || s.grade === "B") && s.action === "buy");
  if (!valid.length) return;
  const s = valid[0];
  const key = `${s.symbol}|${s.time}`;
  if (key === lastNotifiedKey) return;
  lastNotifiedKey = key;
  clientTelegram(`🎯 سیگنال ${s.grade} — ${s.symbol}\nامتیاز: ${s.score}\nورود: ${s.entry} | ضرر: ${s.stop} | سود: ${s.target}`);
}

document.querySelectorAll("#gradePicker .chip").forEach((c) => {
  c.addEventListener("click", () => {
    document.querySelectorAll("#gradePicker .chip").forEach((x) => x.classList.remove("active"));
    c.classList.add("active");
  });
});

$("#saveUrl").addEventListener("click", () => {
  const v = $("#serverUrl").value.trim().replace(/\/$/, "");
  state.serverUrl = v;
  localStorage.setItem("sarkhat_server", v);
  state.cloud = false;        // کاربر آدرس سرور داد — اول سرور را امتحان کن
  loadHealth().then(loadSignals);
});

// تلگرام (اختیاری — همه‌چیز روی گوشی خودت ذخیره می‌شود)
const saveTg = $("#saveTg");
if (saveTg) saveTg.addEventListener("click", () => {
  localStorage.setItem("sarkhat_tg_token", ($("#tgToken").value || "").trim());
  localStorage.setItem("sarkhat_tg_chat", ($("#tgChat").value || "").trim());
  saveTg.textContent = "✓ ذخیره شد";
  setTimeout(() => (saveTg.textContent = "ذخیره تلگرام"), 1500);
});
const testTg = $("#testTg");
if (testTg) testTg.addEventListener("click", () => {
  clientTelegram("✅ تست سرخط‌زن — اتصال تلگرام کار می‌کند");
  testTg.textContent = "ارسال شد — تلگرامت را چک کن";
  setTimeout(() => (testTg.textContent = "ارسال پیام تست"), 2000);
});

$("#saveSettings").addEventListener("click", async () => {
  const body = {
    auto_paper: $("#autoPaper").checked,
    low_data: $("#lowData").checked,
    scan_interval_sec: Math.max(60, parseInt($("#scanInterval").value, 10) || 90),
    min_grade: (document.querySelector("#gradePicker .chip.active") || {}).dataset?.grade || "B",
  };
  try {
    await fetch(api("/api/settings"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    state.settings = { ...(state.settings || {}), ...body };
    scheduleRefresh();
    $("#saveSettings").textContent = "✓ ذخیره شد";
    setTimeout(() => ($("#saveSettings").textContent = "ذخیره تنظیمات"), 1500);
  } catch (e) { setConn(false); }
});

// ---------- نصب PWA ----------
let deferredPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredPrompt = e;
  $("#installBtn").classList.remove("hidden");
});
$("#installBtn").addEventListener("click", async () => {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  await deferredPrompt.userChoice;
  deferredPrompt = null;
  $("#installBtn").classList.add("hidden");
});

// ---------- بروزرسانی دوره‌ای ----------
function scheduleRefresh() {
  if (state.refreshTimer) clearInterval(state.refreshTimer);
  const low = state.settings?.low_data !== false;
  const sec = state.cloud ? 120 : Math.max(60, state.settings?.scan_interval_sec || 90) * (low ? 1 : 1);
  state.refreshTimer = setInterval(() => {
    if (state.cloud) { loadCloud(); maybeNotifySignals(); }
    else loadSignals();
    const active = document.querySelector(".tabbtn.active")?.dataset.tab;
    if (active === "market") loadMarket();
    if (active === "trades") loadTrades();
  }, sec * 1000);
}

// ---------- ثبت سرویس‌ورکر (نصب و کار آفلاین) ----------
if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./sw.js").catch(() => {});
  });
}

// ---------- شروع ----------
(async function init() {
  if (window.BTC) BTC.init();          // موتور بیت‌کوین ۵ دقیقه‌ای — در همه حالت‌ها فعال
  const ok = await loadHealth();
  if (ok) {
    await loadSignals();
    await loadMarket();
    await loadTrades();
    maybeNotifySignals();
    if (!state.cloud) scheduleRefresh();
  } else {
    $("#signalsList").innerHTML = `
      <div class="empty">
        نه سرور محلی در دسترس است نه دیتای ابری.<br><br>
        ۱) اینترنت گوشی را چک کن و صفحه را رفرش کن<br>
        ۲) اگر ربات را روی Termux اجرا کرده‌ای: <b>python main.py serve</b> بزن و بعد رفرش کن<br>
        ۳) یا در تب تنظیمات آدرس سرور را عوض کن
      </div>`;
  }
})();
