let DATA = [];
const APPROVED = "APROVADA NO FILTRO";
const byTicker = () => Object.fromEntries(DATA.map(item => [item.ticker, item]));
const money = value => new Intl.NumberFormat("pt-BR", {style: "currency", currency: "BRL"}).format(Number(value || 0));
const pct = value => value == null ? "—" : `${(Number(value) * 100).toFixed(2).replace(".", ",")}%`;
const num = value => Number(String(value ?? "").replace(/\./g, "").replace(",", "."));
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));

function tag(text) {
  const upper = String(text || "").toUpperCase();
  let css = "ok";
  if (upper.includes("ALERTA") || upper.includes("REPROVADA") || upper.includes("NEGATIVA")) css = "danger";
  else if (upper.includes("PENDENTE")) css = "warn";
  else if (upper.includes("BLOQUEADA") || upper.includes("NÃO APLICÁVEL")) css = "muted";
  return `<span class="tag ${css}">${esc(text || "—")}</span>`;
}

function fillSelect(id, includeEmpty = true) {
  const element = document.getElementById(id);
  if (!element) return;
  const options = [...DATA]
    .sort((a, b) => a.ticker.localeCompare(b.ticker))
    .map(item => `<option value="${item.ticker}">${item.ticker} — ${esc(item.empresa)}</option>`)
    .join("");
  element.innerHTML = (includeEmpty ? '<option value="">Selecione...</option>' : "") + options;
}

function valueOrBlocked(item, field) {
  return item.filtroQualidadeOriginal === APPROVED ? (item[field] || "—") : "Bloqueado pelo filtro";
}

function compare() {
  const map = byTicker();
  const items = ["compare1", "compare2", "compare3"]
    .map(id => map[document.getElementById(id).value])
    .filter(Boolean);
  if (items.length < 2) {
    document.getElementById("comparisonResult").innerHTML = "<p>Selecione pelo menos duas ações.</p>";
    return;
  }
  const rows = [
    ["Empresa", ...items.map(item => item.empresa)],
    ["Filtro", ...items.map(item => tag(item.filtroQualidadeOriginal))],
    ["Classificação 3 pilares", ...items.map(item => tag(item.classificacao3P))],
    ["ROE médio 5A", ...items.map(item => item.roe5a)],
    ["CAGR lucro 5A", ...items.map(item => item.cagrLucro5a)],
    ["P/L", ...items.map(item => item.pl || "—")],
    ["P/L setor", ...items.map(item => item.plSetor || "—")],
    ["DY normalizado 12M", ...items.map(item => item.dy12m || "—")],
    ["Payout", ...items.map(item => item.payout || "—")],
    ["Margem Bazin", ...items.map(item => valueOrBlocked(item, "margemBazin"))],
    ["Margem DCF", ...items.map(item => valueOrBlocked(item, "margemDcf"))],
    ["Dividendos em 5 anos", ...items.map(item => item.anosDividendos5A == null ? "Pendente" : `${item.anosDividendos5A}/5`)],
    ["Diluição 5A", ...items.map(item => `${item.diluicao5A || "—"} — ${item.statusDiluicao || ""}`)],
  ];
  document.getElementById("comparisonResult").innerHTML = `
    <div class="table-scroll"><table class="comparison-table">
      <thead><tr><th>Critério</th>${items.map(item => `<th>${item.ticker}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(row => `<tr>${row.map((value, index) => `<${index ? "td" : "th"}>${value}</${index ? "td" : "th"}>`).join("")}</tr>`).join("")}</tbody>
    </table></div>
    <p><b>Leitura:</b> ${items.map(item => `${item.ticker}: ${item.justificativaClassificacao || "sem classificação avançada"}`).join(" | ")}</p>`;
}

function parsePortfolio() {
  const map = byTicker();
  return document.getElementById("portfolioInput").value
    .split(/\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .map(line => {
      const [tickerRaw, valueRaw] = line.split(/[;,]/);
      const ticker = String(tickerRaw || "").trim().toUpperCase();
      return {ticker, value: num(valueRaw), item: map[ticker]};
    })
    .filter(position => position.item && position.value > 0);
}

function analyzePortfolio() {
  const positions = parsePortfolio();
  localStorage.setItem("valdemarPortfolio", document.getElementById("portfolioInput").value);
  if (!positions.length) {
    document.getElementById("portfolioResult").innerHTML = "<p>Nenhuma posição válida.</p>";
    return;
  }
  const total = positions.reduce((sum, position) => sum + position.value, 0);
  const sectors = {};
  let roeSum = 0;
  let roeCoverage = 0;
  let dySum = 0;
  let dyCoverage = 0;
  positions.forEach(position => {
    position.weight = position.value / total;
    sectors[position.item.segmento] = (sectors[position.item.segmento] || 0) + position.weight;
    if (position.item.roe5aRaw != null) {
      roeSum += position.weight * position.item.roe5aRaw;
      roeCoverage += position.weight;
    }
    if (position.item.dy12mRaw != null) {
      dySum += position.weight * position.item.dy12mRaw;
      dyCoverage += position.weight;
    }
  });
  const hhi = positions.reduce((sum, position) => sum + (position.weight * 100) ** 2, 0);
  const risks = [];
  positions.filter(position => position.weight > 0.20).forEach(position => risks.push(`Posição ${position.ticker} representa ${(position.weight * 100).toFixed(1)}%, acima do alerta de 20%.`));
  Object.entries(sectors).filter(([, weight]) => weight > 0.35).forEach(([sector, weight]) => risks.push(`Setor ${sector} representa ${(weight * 100).toFixed(1)}%, acima do alerta de 35%.`));
  positions.filter(position => position.item.filtroQualidadeOriginal !== APPROVED).forEach(position => risks.push(`${position.ticker}: ${position.item.filtroQualidadeOriginal}; valuation e projeção bloqueados.`));
  document.getElementById("portfolioResult").innerHTML = `
    <div class="metric-cards">
      <div class="metric-card"><span>Patrimônio informado</span><strong>${money(total)}</strong></div>
      <div class="metric-card"><span>HHI simplificado</span><strong>${hhi.toFixed(0)}</strong></div>
      <div class="metric-card"><span>ROE ponderado</span><strong>${roeCoverage ? pct(roeSum / roeCoverage) : "Pendente"}</strong><small>Cobertura ${pct(roeCoverage)}</small></div>
      <div class="metric-card"><span>DY ponderado</span><strong>${dyCoverage ? pct(dySum / dyCoverage) : "Pendente"}</strong><small>Cobertura ${pct(dyCoverage)}</small></div>
    </div>
    <div class="table-scroll"><table class="portfolio-table"><thead><tr><th>Ticker</th><th>Valor</th><th>Peso</th><th>Setor</th><th>Qualidade</th><th>Bazin</th><th>DCF</th></tr></thead>
      <tbody>${positions.map(position => `<tr><td>${position.ticker}</td><td>${money(position.value)}</td><td>${pct(position.weight)}</td><td>${esc(position.item.segmento)}</td><td>${tag(position.item.filtroQualidadeOriginal)}</td><td>${esc(valueOrBlocked(position.item, "margemBazin"))}</td><td>${esc(valueOrBlocked(position.item, "margemDcf"))}</td></tr>`).join("")}</tbody>
    </table></div>
    <h3>Riscos</h3>${risks.length ? `<ul class="risk-list">${risks.map(risk => `<li>${esc(risk)}</li>`).join("")}</ul>` : "<p>Nenhum alerta mecânico de concentração ou qualidade.</p>"}
    <p><b>Leitura educacional:</b> médias usam apenas posições com dados disponíveis; a cobertura evita tratar ausências como zero.</p>`;
}

function watchItems() {
  try { return JSON.parse(localStorage.getItem("valdemarWatchlist") || "[]"); }
  catch { return []; }
}

function saveWatch(items) {
  localStorage.setItem("valdemarWatchlist", JSON.stringify(items));
  renderWatch();
}

function watchStatus(item, trigger) {
  if (!item) return "Pendente de dados";
  if (item.filtroQualidadeOriginal !== APPROVED) return "Bloqueada pelo filtro";
  if (!(item.precoRaw > 0) || !(trigger > 0)) return "Pendente de dados";
  return item.precoRaw <= trigger
    ? "Aprovada — condição matemática atingida"
    : "Aprovada — gatilho não atingido";
}

function renderWatch() {
  const map = byTicker();
  const items = watchItems();
  document.getElementById("watchlistResult").innerHTML = items.length ? `
    <div class="table-scroll"><table class="watch-table"><thead><tr><th>Ticker</th><th>Fechamento B3</th><th>Gatilho pessoal</th><th>Status neutro</th><th>Filtro</th><th>Referência Bazin</th><th>Atualização</th><th></th></tr></thead>
      <tbody>${items.map((watch, index) => {
        const item = map[watch.ticker];
        return `<tr><td>${watch.ticker}</td><td>${item?.preco || "—"}</td><td>${money(watch.trigger)}</td><td>${tag(watchStatus(item, watch.trigger))}</td><td>${tag(item?.filtroQualidadeOriginal || "Pendente")}</td><td>${item?.filtroQualidadeOriginal === APPROVED ? (item.precoTetoBazin || "—") : "Bloqueada"}</td><td>${item?.dataCotacao || "—"}</td><td><button data-remove-watch="${index}">Remover</button></td></tr>`;
      }).join("")}</tbody>
    </table></div>` : "<p>Watchlist vazia.</p>";
  document.querySelectorAll("[data-remove-watch]").forEach(button => {
    button.onclick = () => {
      const list = watchItems();
      list.splice(Number(button.dataset.removeWatch), 1);
      saveWatch(list);
    };
  });
}

function addWatch() {
  const ticker = document.getElementById("watchTicker").value;
  const trigger = Number(document.getElementById("watchTrigger").value);
  if (!ticker || !(trigger > 0)) return;
  const list = watchItems();
  const found = list.find(item => item.ticker === ticker);
  if (found) found.trigger = trigger;
  else list.push({ticker, trigger});
  saveWatch(list);
}

function dividendProjection() {
  const item = byTicker()[document.getElementById("projectionTicker").value];
  const shares = Number(document.getElementById("projectionShares").value);
  const target = document.getElementById("dividendProjectionResult");
  if (!item || !(shares > 0)) {
    target.innerHTML = "<p>Selecione uma ação e informe a quantidade.</p>";
    return;
  }
  if (item.filtroQualidadeOriginal !== APPROVED) {
    target.innerHTML = `<p>Projeção bloqueada: ${esc(item.filtroQualidadeOriginal)}.</p>`;
    return;
  }
  if (item.dpa12mRaw == null || item.cagrDpa5ARaw == null) {
    target.innerHTML = "<p>Projeção bloqueada: DPA normalizado ou histórico completo de cinco anos não conciliado.</p>";
    return;
  }
  const historical = Number(item.cagrDpa5ARaw);
  const scenarios = [
    ["Conservador", historical * 0.5],
    ["Base", Math.min(historical, 0.06)],
    ["Otimista", Math.min(historical * 1.25, 0.10)],
  ];
  const years = [5, 10];
  target.innerHTML = `<div class="table-scroll"><table class="projection-table"><thead><tr><th>Cenário</th><th>Crescimento</th>${years.map(year => `<th>DPA ${year}A</th><th>Renda anual ${year}A</th>`).join("")}</tr></thead>
    <tbody>${scenarios.map(([name, growth]) => `<tr><td>${name}</td><td>${pct(growth)}</td>${years.map(year => { const dpa = item.dpa12mRaw * Math.pow(1 + growth, year); return `<td>${money(dpa)}</td><td>${money(dpa * shares)}</td>`; }).join("")}</tr>`).join("")}</tbody>
  </table></div><p>Base: DPA normalizado ${item.dpa12m}; CAGR histórico ${item.cagrDpa5A}. Projeção meramente ilustrativa, baseada em premissas históricas que podem não se repetir. Não constitui garantia de retorno.</p>`;
}

function futureValue(initial, monthly, annual, years) {
  const rate = Math.pow(1 + annual, 1 / 12) - 1;
  const periods = years * 12;
  return initial * Math.pow(1 + rate, periods) + (rate ? monthly * (Math.pow(1 + rate, periods) - 1) / rate : monthly * periods);
}

function wealthProjection() {
  const initial = Number(document.getElementById("initialAmount").value || 0);
  const monthly = Number(document.getElementById("monthlyContribution").value || 0);
  const years = Number(document.getElementById("projectionYears").value || 10);
  const scenarios = [["Conservador", 0.06], ["Base", 0.10], ["Otimista", 0.14]];
  document.getElementById("wealthProjectionResult").innerHTML = `<div class="table-scroll"><table class="projection-table"><thead><tr><th>Cenário</th><th>Retorno anual assumido</th><th>Patrimônio estimado</th></tr></thead>
    <tbody>${scenarios.map(([name, rate]) => `<tr><td>${name}</td><td>${pct(rate)}</td><td>${money(futureValue(initial, monthly, rate, years))}</td></tr>`).join("")}</tbody>
  </table></div><p>Projeção meramente ilustrativa. Não constitui garantia de retorno.</p>`;
}

function setupTabs() {
  document.querySelectorAll("[data-mode]").forEach(button => {
    button.onclick = () => {
      document.querySelectorAll("[data-mode]").forEach(item => item.classList.toggle("active", item === button));
      document.querySelectorAll(".mode-panel").forEach(panel => panel.classList.toggle("active", panel.id === button.dataset.mode));
    };
  });
}

async function load() {
  const response = await fetch(`data.json?t=${Date.now()}`, {cache: "no-store"});
  const payload = await response.json();
  DATA = payload.items || [];
  ["compare1", "compare2", "compare3", "watchTicker", "projectionTicker"].forEach(id => fillSelect(id, true));
  document.getElementById("dataStatus").textContent = `Fechamento B3 ${payload.latestQuoteDate || "—"} | Selic ${payload.macro?.selic || "pendente"} | ${DATA.length} empresas.`;
  const saved = localStorage.getItem("valdemarPortfolio");
  if (saved) document.getElementById("portfolioInput").value = saved;
  renderWatch();
}

setupTabs();
document.getElementById("compareButton").onclick = compare;
document.getElementById("portfolioButton").onclick = analyzePortfolio;
document.getElementById("watchAdd").onclick = addWatch;
document.getElementById("dividendProjectionButton").onclick = dividendProjection;
document.getElementById("wealthProjectionButton").onclick = wealthProjection;
load().catch(error => { document.getElementById("dataStatus").textContent = `Erro: ${error.message}`; });
