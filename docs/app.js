let DATA = [];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function badgeClass(status) {
  if (status === "SIM" || String(status).startsWith("APROVADA")) return "ok";
  if (status === "ALERTA VERMELHO") return "danger";
  if (status === "FORA DO UNIVERSO INICIAL" || status === "NÃO") return "no";
  return "pending";
}

function criteriaHtml(criteria) {
  if (!criteria?.length) return '<p class="muted">Critérios ainda não disponíveis.</p>';
  return `<div class="criteria">${criteria.map(c => `
    <article class="criterion">
      <div><strong>${esc(c.name)}</strong><small>Limite: ${esc(c.limit)}</small></div>
      <div class="criterion-value">${esc(c.value)}</div>
      <span class="pill ${badgeClass(c.status)}">${esc(c.status)}</span>
      ${c.note ? `<p>${esc(c.note)}</p>` : ""}
    </article>`).join("")}</div>`;
}

function detailsRow(item) {
  return `<tr class="details-row" data-details="${esc(item.ticker)}"><td colspan="11">
    <div class="details-panel">
      <div class="fund-grid">
        <div><span>Receita LTM</span><strong>${esc(item.receitaLtm)}</strong></div>
        <div><span>Lucro líquido LTM</span><strong>${esc(item.lucroLtm)}</strong></div>
        <div><span>EBITDA LTM</span><strong>${esc(item.ebitdaLtm)}</strong></div>
        <div><span>Patrimônio líquido</span><strong>${esc(item.patrimonio)}</strong></div>
        <div><span>Caixa</span><strong>${esc(item.caixa)}</strong></div>
        <div><span>Dívida bruta</span><strong>${esc(item.dividaBruta)}</strong></div>
        <div><span>Dívida líquida</span><strong>${esc(item.dividaLiquida)}</strong></div>
        <div><span>Payout</span><strong>${esc(item.payout)}</strong></div>
      </div>
      <div class="quality-note"><b>Conclusão do filtro:</b> ${esc(item.motivoQualidade)}</div>
      ${criteriaHtml(item.criterios)}
      <div class="sources">
        <span><b>CNPJ:</b> ${esc(item.cnpj)}</span>
        <span><b>Código CVM:</b> ${esc(item.codigoCvm)}</span>
        <span><b>Origem:</b> ${esc(item.origemFundamentos)}</span>
        <span><b>Fonte mercado:</b> ${esc(item.fonteCotacao)}</span>
        <span><b>Fonte fundamentos:</b> ${esc(item.fonteFundamentos)}</span>
      </div>
    </div>
  </td></tr>`;
}

function filteredRows() {
  const query = document.getElementById("q")?.value.trim().toUpperCase() || "";
  const filter = document.getElementById("filter")?.value || "";
  const sort = document.getElementById("sort")?.value || "liquidity";
  let rows = DATA.filter(item => {
    const matchesQuery = !query || `${item.ticker} ${item.empresa}`.toUpperCase().includes(query);
    let matchesFilter = true;
    if (filter === "INITIAL") matchesFilter = item.elegivelInicial === "SIM";
    if (filter === "APPROVED") matchesFilter = item.elegivelInicial === "SIM" && String(item.filtroQualidadeOriginal || "").startsWith("APROVADA");
    if (filter === "RED") matchesFilter = item.elegivelInicial === "SIM" && item.filtroQualidadeOriginal === "ALERTA VERMELHO";
    if (filter === "PENDING") matchesFilter = item.elegivelInicial === "SIM" && String(item.filtroQualidadeOriginal || "").startsWith("PENDENTE");
    if (filter === "OUT") matchesFilter = item.elegivelInicial !== "SIM";
    return matchesQuery && matchesFilter;
  });
  rows = [...rows].sort((a, b) => {
    if (sort === "ticker") return a.ticker.localeCompare(b.ticker);
    if (sort === "quality") return (b.scoreQualidade ?? -1) - (a.scoreQualidade ?? -1);
    if (sort === "roe") return (b.roe5aRaw ?? -999) - (a.roe5aRaw ?? -999);
    return (b.volume20Raw ?? 0) - (a.volume20Raw ?? 0);
  });
  return rows;
}

function render() {
  const body = document.getElementById("body");
  if (!body) return;
  const rows = filteredRows();
  body.innerHTML = rows.map(item => `
    <tr class="main-row" data-ticker="${esc(item.ticker)}" tabindex="0">
      <td><strong>${esc(item.ticker)}</strong><small>${esc(item.segmento)}</small></td>
      <td>${esc(item.empresa)}</td>
      <td>${esc(item.preco)}<small>${esc(item.variacao)}</small></td>
      <td><span class="pill ${badgeClass(item.elegivelInicial)}">${esc(item.elegivelInicial)}</span><small>${esc(item.motivoInicial)}</small></td>
      <td><span class="pill ${badgeClass(item.filtroQualidade)}">${esc(item.filtroQualidade)}</span>${item.scoreQualidade !== null ? `<small>Score ${esc(item.scoreQualidade)}/100</small>` : ""}</td>
      <td>${esc(item.roe5a)}</td><td>${esc(item.cagrLucro5a)}</td><td>${esc(item.dlEbitda)}</td>
      <td>${esc(item.margemLiquida)}<small>${esc(item.tendenciaMargem)}</small></td>
      <td>${esc(item.lucrosPositivos5a ?? "—")}/5</td><td>${esc(item.referenciaFundamentos)}</td>
    </tr>${detailsRow(item)}`).join("");

  document.querySelectorAll(".details-row").forEach(row => row.hidden = true);
  document.querySelectorAll(".main-row").forEach(row => {
    const toggle = () => {
      const details = document.querySelector(`[data-details="${CSS.escape(row.dataset.ticker)}"]`);
      if (!details) return;
      details.hidden = !details.hidden;
      row.classList.toggle("open", !details.hidden);
    };
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") toggle();
    });
  });
}

function formatGeneratedAt(value) {
  if (!value) return "-";
  try {
    return new Intl.DateTimeFormat("pt-BR", {
      timeZone: "America/Manaus", dateStyle: "short", timeStyle: "short"
    }).format(new Date(value));
  } catch { return value; }
}

async function load() {
  try {
    const response = await fetch(`data.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    DATA = payload.items || [];
    setText("count", payload.count ?? 0);
    setText("initialEligible", payload.initialEligible ?? 0);
    setText("qualityApproved", payload.qualityPartialApproved ?? 0);
    setText("redAlerts", payload.redAlerts ?? 0);
    setText("pending", payload.pendingFundamentals ?? 0);
    setText("quoteDate", payload.latestQuoteDate || "-");
    setText("updated", `Gerado em ${formatGeneratedAt(payload.generatedAt)}`);
    setText("disclaimer", payload.disclaimer || "");
    const method = payload.methodology || {};
    setText("methodInitial", method.initial || "");
    setText("methodQuality", method.quality || "");
    setText("methodFinancial", method.financial || "");
    setText("methodValuation", method.valuation || "");
    render();
  } catch (error) {
    setText("updated", `Erro ao carregar dados: ${error.message}`);
  }
}

["q", "filter", "sort"].forEach(id => {
  const element = document.getElementById(id);
  if (element) element.addEventListener(id === "q" ? "input" : "change", render);
});
load();
