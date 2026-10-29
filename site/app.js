// Dashboard estático: lee data.json (lo genera `python -m copytrader export` en GitHub Actions).
// Todo texto externo (símbolos de tokens, motivos...) se inserta con textContent, nunca como HTML.

const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
let DATA;

// --- Formato --------------------------------------------------------------------------------

const cur = () => (DATA.currency === "EUR" ? "€" : "$");
const money = (v, sign = false) =>
  v == null ? "–" : `${sign && v > 0 ? "+" : ""}${v.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${cur()}`;
const pct = (v, sign = false) =>
  v == null ? "–" : `${sign && v > 0 ? "+" : ""}${(v * 100).toLocaleString("es-ES", { maximumFractionDigits: 1 })} %`;
const num = (v, d = 2) => {
  if (v == null) return "–";
  const r = Math.round(Number(v) * 10 ** d) / 10 ** d;
  return (r === 0 ? 0 : r).toLocaleString("es-ES", { maximumFractionDigits: d }); // evita "-0"
};
const short = (a) => (a ? `${a.slice(0, 4)}…${a.slice(-4)}` : "–");
const dateTime = (t) => new Date(t).toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const ago = (t) => {
  const m = Math.round((Date.now() - new Date(t)) / 60000);
  return m < 60 ? `hace ${m} min` : m < 1440 ? `hace ${Math.round(m / 60)} h` : `hace ${Math.round(m / 1440)} d`;
};
// eps: por debajo se considera cero (lo que se muestra redondeado a 0 no se colorea)
const signClass = (v, eps = 0.005) => (v > eps ? "up" : v < -eps ? "down" : "");

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else e.setAttribute(k, v);
  }
  for (const c of children) if (c != null) e.append(c);
  return e;
}

const solscan = (kind, id, label) =>
  el("a", { href: `https://solscan.io/${kind}/${id}`, target: "_blank", rel: "noopener", text: label ?? short(id) });

/** Tabla genérica: columns = [{ head, cell(row) -> Node|string, num? }] */
function table(rows, columns, emptyText) {
  if (!rows.length) return el("p", { class: "empty", text: emptyText });
  const thead = el("tr", {}, ...columns.map((c) => el("th", { class: c.num ? "num" : "", text: c.head })));
  const body = rows.map((r) =>
    el("tr", {}, ...columns.map((c) => {
      const v = c.cell(r);
      const td = el("td", { class: [c.num ? "num" : "", c.cls?.(r) ?? ""].join(" ").trim() });
      td.append(v instanceof Node ? v : String(v ?? "–"));
      return td;
    })),
  );
  return el("table", {}, el("thead", {}, thead), el("tbody", {}, ...body));
}

// --- Tooltip --------------------------------------------------------------------------------

const tip = $("tooltip");
function showTip(evt, lines) {
  tip.replaceChildren(...lines.map(([b, rest], i) => el("div", {}, el("b", { text: b }), rest ? ` ${rest}` : "")));
  tip.hidden = false;
  const x = Math.min(evt.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  tip.style.left = `${x}px`;
  tip.style.top = `${evt.clientY + 14}px`;
}
const hideTip = () => (tip.hidden = true);

function svgEl(tag, attrs) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

function niceTicks(min, max, count = 4) {
  const span = max - min || Math.abs(max) || 1;
  const step = 10 ** Math.floor(Math.log10(span / count));
  const mult = [1, 2, 2.5, 5, 10].find((m) => span / (step * m) <= count) ?? 10;
  const s = step * mult;
  const ticks = [+(Math.floor(min / s) * s).toFixed(10)];
  while (ticks.at(-1) < max) ticks.push(+(ticks.at(-1) + s).toFixed(10)); // el último tick cubre el máximo
  return ticks;
}

// --- Gráfica: valor de la cartera (línea, crosshair + tooltip) -------------------------------

function equityChart() {
  const box = $("equity-chart");
  const pts = DATA.equity_curve.map((p) => ({ t: new Date(p.t), v: p.equity }));
  const W = Math.max(box.clientWidth, 320), H = 240, M = { l: 56, r: 12, t: 10, b: 26 };
  const t0 = +pts[0].t, t1 = Math.max(+pts.at(-1).t, t0 + 3600e3);
  const vals = [...pts.map((p) => p.v), DATA.initial_capital];
  const ticks = niceTicks(Math.min(...vals) * 0.98, Math.max(...vals) * 1.02);
  const [y0, y1] = [ticks[0], ticks.at(-1)];
  const x = (t) => M.l + ((+t - t0) / (t1 - t0)) * (W - M.l - M.r);
  const y = (v) => M.t + (1 - (v - y0) / (y1 - y0)) * (H - M.t - M.b);

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Evolución del valor de la cartera" });
  const grid = svgEl("g", { class: "grid" });
  for (const v of ticks) {
    grid.append(svgEl("line", { x1: M.l, x2: W - M.r, y1: y(v), y2: y(v) }));
    const label = svgEl("text", { x: M.l - 8, y: y(v) + 4, "text-anchor": "end", class: "axis" });
    label.textContent = num(v, 0);
    grid.append(label);
  }
  svg.append(grid);
  for (const t of [t0, t1]) {
    const label = svgEl("text", { x: x(t), y: H - 6, "text-anchor": t === t0 ? "start" : "end", class: "axis" });
    label.textContent = new Date(t).toLocaleDateString("es-ES", { day: "2-digit", month: "short" });
    svg.append(label);
  }
  svg.append(svgEl("line", {
    x1: M.l, x2: W - M.r, y1: y(DATA.initial_capital), y2: y(DATA.initial_capital),
    stroke: "var(--muted)", "stroke-width": 1.5, "stroke-dasharray": "4 4",
  }));
  svg.append(svgEl("path", {
    d: pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(""),
    fill: "none", stroke: "var(--series-1)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round",
  }));
  const last = pts.at(-1);
  svg.append(svgEl("circle", { cx: x(last.t), cy: y(last.v), r: 4, fill: "var(--series-1)", stroke: "var(--surface)", "stroke-width": 2 }));

  const cross = svgEl("line", { y1: M.t, y2: H - M.b, stroke: "var(--axis)", "stroke-width": 1, visibility: "hidden" });
  const dot = svgEl("circle", { r: 4, fill: "var(--series-1)", stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" });
  const hit = svgEl("rect", { x: M.l, y: 0, width: W - M.l - M.r, height: H, fill: "transparent" });
  svg.append(cross, dot, hit);
  hit.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const p = pts.reduce((a, b) => (Math.abs(x(b.t) - px) < Math.abs(x(a.t) - px) ? b : a));
    for (const n of [cross, dot]) n.setAttribute("visibility", "visible");
    cross.setAttribute("x1", x(p.t)); cross.setAttribute("x2", x(p.t));
    dot.setAttribute("cx", x(p.t)); dot.setAttribute("cy", y(p.v));
    showTip(e, [[money(p.v)], [dateTime(p.t), pct(p.v / DATA.initial_capital - 1, true)]]);
  });
  hit.addEventListener("pointerleave", () => {
    for (const n of [cross, dot]) n.setAttribute("visibility", "hidden");
    hideTip();
  });
  box.replaceChildren(svg);
}

// --- Gráfica: PnL por wallet (barras horizontales divergentes) -------------------------------

function walletChart() {
  const box = $("wallet-chart");
  const byWallet = DATA.audits[0]?.metrics?.by_wallet ?? {};
  const rows = Object.entries(byWallet).map(([w, s]) => ({ w, ...s }));
  if (!rows.length) return box.replaceChildren(el("p", { class: "empty", text: "Aún no hay rondas copiadas que auditar." }));

  const W = Math.max(box.clientWidth, 320), row = 28, M = { l: 96, r: 72, t: 4, b: 4 };
  const H = M.t + M.b + rows.length * row;
  const max = Math.max(...rows.map((r) => Math.abs(r.pnl)), 1);
  const hasNeg = rows.some((r) => r.pnl < 0);
  const zero = hasNeg ? M.l + (W - M.l - M.r) / 2 : M.l;
  const scale = (hasNeg ? (W - M.l - M.r) / 2 : W - M.l - M.r) / max;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "PnL por wallet copiada" });
  rows.forEach((r, i) => {
    const cy = M.t + i * row + row / 2;
    const len = Math.max(Math.abs(r.pnl) * scale, 2);
    const g = svgEl("g", {});
    const label = svgEl("text", { x: M.l - 8, y: cy + 4, "text-anchor": "end", class: "axis" });
    label.textContent = short(r.w);
    g.append(label);
    g.append(svgEl("rect", {
      x: r.pnl >= 0 ? zero : zero - len, y: cy - 8, width: len, height: 16, rx: 3,
      fill: r.pnl >= 0 ? "var(--pos)" : "var(--neg)",
    }));
    const val = svgEl("text", {
      x: r.pnl >= 0 ? zero + len + 6 : zero - len - 6, y: cy + 4,
      "text-anchor": r.pnl >= 0 ? "start" : "end", class: "axis",
    });
    val.textContent = money(r.pnl, true);
    g.append(val);
    g.append(svgEl("rect", { x: 0, y: cy - row / 2, width: W, height: row, fill: "transparent" }));
    g.addEventListener("pointermove", (e) =>
      showTip(e, [[short(r.w)], [money(r.pnl, true), `ROI ${pct(r.roi, true)}`], [`${r.n} rondas`, r.win_rate == null ? "" : `· aciertos ${pct(r.win_rate)}`]]));
    g.addEventListener("pointerleave", hideTip);
    svg.append(g);
  });
  svg.append(svgEl("line", { x1: zero, x2: zero, y1: 0, y2: H, class: "baseline", "stroke-width": 1 }));
  box.replaceChildren(svg);
}

// --- Secciones ------------------------------------------------------------------------------

function tiles() {
  const p = DATA.portfolio;
  const trades = DATA.trades;
  const count = (s) => trades.filter((t) => t.status === s).length;
  const tile = (label, value, sub, cls = "") =>
    el("div", { class: "tile" }, el("div", { class: "label", text: label }), el("div", { class: `value ${cls}`, text: value }), sub ? el("div", { class: "sub", text: sub }) : null);
  $("tiles").replaceChildren(
    tile("Cartera", money(p.equity), `capital inicial ${money(DATA.initial_capital)}`),
    tile("Rentabilidad", pct(p.return, true), money(p.equity - DATA.initial_capital, true), signClass(p.return)),
    tile("Cash", money(p.cash), `${pct(p.cash / p.equity)} de la cartera`),
    tile("En posiciones", money(p.positions_value), `${DATA.positions.length} abiertas`),
    tile("Operaciones", String(count("filled")), `${count("failed")} fallidas · ${count("skipped")} omitidas`),
  );
}

function trades() {
  const filter = $("trade-filter").value;
  const rows = DATA.trades.filter((t) => !filter || t.status === filter);
  $("trades").replaceChildren(table(rows, [
    { head: "Fecha", cell: (t) => dateTime(t.executed_at) },
    { head: "Estado", cell: (t) => el("span", { class: `badge ${t.status}`, text: { filled: "ejecutada", failed: "fallida", skipped: "omitida" }[t.status] }) },
    { head: "Lado", cell: (t) => (t.side === "buy" ? "compra" : "venta") },
    { head: "Token", cell: (t) => solscan("token", t.token_mint, t.symbol || short(t.token_mint)) },
    { head: "Wallet", cell: (t) => solscan("account", t.source_wallet) },
    { head: "Importe", num: true, cell: (t) => money(t.value) },
    { head: "Fee", num: true, cell: (t) => money(t.fee) },
    { head: "Impact", num: true, cell: (t) => pct(t.price_impact_pct) },
    { head: "Retraso", num: true, cell: (t) => (t.delay_s == null ? "–" : t.delay_s < 120 ? `${num(t.delay_s, 0)} s` : `${num(t.delay_s / 60, 0)} min`) },
    { head: "Motivo", cell: (t) => t.allocator_reason ?? "–" },
    { head: "Tx whale", cell: (t) => solscan("tx", t.source_signature) },
  ], "Todavía no hay operaciones simuladas. Aparecerán cuando las wallets del top operen."));
}

function positions() {
  $("positions").replaceChildren(table(DATA.positions, [
    { head: "Token", cell: (p) => solscan("token", p.token_mint, p.symbol || short(p.token_mint)) },
    { head: "Wallet origen", cell: (p) => solscan("account", p.source_wallet) },
    { head: "Abierta", cell: (p) => dateTime(p.opened_at) },
    { head: "Coste", num: true, cell: (p) => money(p.cost_basis) },
    { head: "Valor", num: true, cell: (p) => money(p.value) },
    { head: "PnL", num: true, cell: (p) => money(p.pnl, true), cls: (p) => signClass(p.pnl) },
    { head: "Liquidez token", num: true, cell: (p) => (p.liquidity_usd == null ? "–" : `${num(p.liquidity_usd, 0)} $`) },
  ], "No hay posiciones abiertas."));
}

function ranking() {
  const ranked = DATA.ranking.filter((r) => r.rank != null);
  $("ranking-note").textContent = `· ${ranked.length} elegibles de ${DATA.ranking.length} puntuadas · top ${DATA.config.portfolio.top_n} se copia`;
  const exclusion = { pocos_trades: "pocos trades", historial_corto: "historial corto", pocas_posiciones_cerradas: "pocas cerradas", pnl_negativo: "PnL negativo", inactiva: "inactiva", wash: "wash trading", bot: "bot" };
  $("ranking").replaceChildren(table(DATA.ranking, [
    { head: "#", num: true, cell: (r) => r.rank ?? "–" },
    { head: "Wallet", cell: (r) => solscan("account", r.address) },
    { head: "Fuente", cell: (r) => r.source },
    { head: "Score", num: true, cell: (r) => num(r.final_score, 3) },
    { head: "PnL 30d (SOL)", num: true, cell: (r) => num(r.metrics.realized_pnl_sol, 1), cls: (r) => signClass(r.metrics.realized_pnl_sol, 0.05) },
    { head: "ROI", num: true, cell: (r) => pct(r.metrics.roi) },
    { head: "Aciertos", num: true, cell: (r) => pct(r.metrics.win_rate) },
    { head: "Cerradas", num: true, cell: (r) => r.metrics.n_closed },
    { head: "Último swap", num: true, cell: (r) => (r.metrics.days_since_last_swap == null ? "–" : `${num(r.metrics.days_since_last_swap, 1)} d`) },
    { head: "Copia", num: true, cell: (r) => num(r.copy_score, 2) },
    { head: "Estado", cell: (r) => (r.metrics.exclusion ? exclusion[r.metrics.exclusion] ?? r.metrics.exclusion : r.rank <= DATA.config.portfolio.top_n ? "copiando" : "elegible") },
  ], "Sin datos de ranking."));
  $("wallets").replaceChildren(table(DATA.wallets, [
    { head: "Fuente", cell: (w) => w.source },
    { head: "Estado", cell: (w) => w.estado },
    { head: "Wallets", num: true, cell: (w) => w.n },
  ], "Sin wallets."));
}

function audits() {
  walletChart();
  const items = DATA.audits.map((a) => {
    const body = el("div", { class: "body" });
    body.innerHTML = DOMPurify.sanitize(marked.parse(a.report_md || ""));
    return el("details", { class: "audit" },
      el("summary", {}, el("span", { text: a.day }), el("span", { class: "muted", text: money(a.equity) })),
      body);
  });
  if (items[0]) items[0].open = true;
  $("audits").replaceChildren(...(items.length ? items : [el("p", { class: "empty", text: "Todavía no hay auditorías (se generan cada día a las 23:47 UTC)." })]));
}

function config() {
  $("config").textContent = JSON.stringify(DATA.config, null, 2);
  const w = DATA.webhook;
  $("webhook").replaceChildren(w
    ? el("div", {}, el("p", { class: "muted small", text: `Actualizado ${ago(w.updated_at)}` }),
      el("ul", {}, ...w.addresses.map((a) => el("li", {}, solscan("account", a, a)))))
    : el("p", { class: "empty", text: "Webhook sin configurar." }));
}

// --- Arranque -------------------------------------------------------------------------------

function selectTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", b.dataset.tab === name);
  for (const s of document.querySelectorAll(".tab")) s.hidden = s.id !== `tab-${name}`;
  if (name === "audits") walletChart();
  try { localStorage.setItem("tab", name); } catch { /* sin almacenamiento */ }
}

async function main() {
  try {
    DATA = await (await fetch(`data.json?ts=${Date.now()}`)).json();
  } catch {
    $("updated").textContent = "No se pudo cargar data.json.";
    return;
  }
  $("updated").textContent = `Actualizado ${dateTime(DATA.generated_at)} (${ago(DATA.generated_at)})`;
  tiles();
  equityChart();
  trades();
  positions();
  ranking();
  audits();
  config();
  $("trade-filter").addEventListener("change", trades);
  for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => selectTab(b.dataset.tab));
  let saved = null;
  try { saved = localStorage.getItem("tab"); } catch { /* sin almacenamiento */ }
  if (saved && $(`tab-${saved}`)) selectTab(saved);
  let resize;
  window.addEventListener("resize", () => {
    clearTimeout(resize);
    resize = setTimeout(() => { equityChart(); walletChart(); }, 150);
  });
}

main();
