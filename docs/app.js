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
  const text = String(status || "").toUpperCase();
  if (
    text === "SIM" || text === "OK" || text === "APROVADO" ||
    text.startsWith("APROVADA") || text.startsWith("LIBERADO") ||
    text.startsWith("ATRATIVA")
  ) return "ok";
  if (
    text === "ALERTA VERMELHO" || text === "REPROVADO" ||
    text.startsWith("CARA")
  ) return "danger";
  if (
    text === "FORA DO UNIVERSO INICIAL" || text === "NÃO" ||
    text.startsWith("BLOQUEADO")
  ) return "no";
  return "pending";
}

function bazinDisplayStatus(item) {
  const valuation = String(item.valuationStatus || "");
  if (valuation.startsWith("BLOQUEADO")) return "BLOQUEADO";
  return item.statusBazin || "PENDENTE";
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

function dividendsHtml(events) {
  if (!events?.length) {
    return '<p class="empty-state">Nenhum dividendo ou JCP elegível encontrado na janela de 12 meses, ou a consulta ainda está pendente.</p>';
  }
  return `<div class="events-table-wrap"><table class="events-table">
    <thead><tr><th>Tipo</th><th>Última data COM</th><th>Pagamento</th><th>Valor por ação</th><th>Período</th></tr></thead>
    <tbody>${events.map(event => `<tr>
      <td>${esc(event.tipo)}</td>
      <td>${esc(event.dataCom || "—")}</td>
      <td>${esc(event.pagamento || "—")}</td>
      <td>${esc(event.valor)}</td>
      <td>${esc(event.periodo || "—")}</td>
    </tr>`).join("")}</tbody>
  </table></div>`;
}

function detailsRow(item) {
  const liquidityLabel = item.elegivelInicial === "SIM" ? "OK" : "REPROVADA";
  const valuation = item.valuationStatus || "BLOQUEADO — aguardando conciliação";
  return `<tr class="details-row" data-details="${esc(item.ticker)}"><td colspan="11">
    <div class="details-panel">
      <div class="status-strip">
        <div class="status-step">
          <span>Etapa 1</span><strong>Liquidez e negociação</strong>
          <span class="pill ${badgeClass(liquidityLabel)}">${esc(liquidityLabel)}</span>
        </div>
        <div class="status-step">
          <span>Etapa 2</span><strong>Qualidade fundamentalista</strong>
          <span class="pill ${badgeClass(item.filtroQualidade)}">${esc(item.filtroQualidade)}</span>
        </div>
        <div class="status-step">
          <span>Etapa 3</span><strong>Payout e Bazin</strong>
          <span class="pill ${badgeClass(valuation)}">${esc(valuation)}</span>
        </div>
      </div>

      <div class="section-title">Indicadores financeiros</div>
      <div class="fund-grid financial-grid">
        <div><span>Receita LTM</span><strong>${esc(item.receitaLtm || "—")}</strong></div>
        <div><span>Lucro líquido LTM</span><strong>${esc(item.lucroLtm || "—")}</strong></div>
        <div><span>EBITDA LTM</span><strong>${esc(item.ebitdaLtm || "—")}</strong></div>
        <div><span>Patrimônio líquido</span><strong>${esc(item.patrimonio || "—")}</strong></div>
        <div><span>Caixa</span><strong>${esc(item.caixa || "—")}</strong></div>
        <div><span>Dívida líquida</span><strong>${esc(item.dividaLiquida || "—")}</strong></div>
      </div>

      <div class="section-title">Dividendos e memória de cálculo Bazin</div>
      <div class="fund-grid bazin-grid">
        <div><span>DPA 12M</span><strong>${esc(item.dpa12m || "Pendente")}</strong><small>${esc(item.quantidadeEventos12m ?? 0)} evento(s)</small></div>
        <div><span>Dividend yield 12M</span><strong>${esc(item.dy12m || "—")}</strong><small>sobre o fechamento indicado</small></div>
        <div><span>LPA estimado</span><strong>${esc(item.lpa || "Pendente")}</strong><small>DRE CVM por classe; fallback lucro LTM ÷ ações</small></div>
        <div><span>Payout estimado</span><strong>${esc(item.payout || "Pendente")}</strong><small>limite do filtro: &lt; 90,00%</small></div>
        <div><span>Preço-teto Bazin</span><strong>${esc(item.precoTetoBazin || "Bloqueado")}</strong><small>DPA 12M ÷ 7,75%</small></div>
        <div><span>Margem Bazin</span><strong>${esc(item.margemBazin || "—")}</strong><small>${esc(bazinDisplayStatus(item))}</small></div>
      </div>
      <div class="window-note">
        <b>Janela dos proventos:</b> ${esc(item.janelaProventosInicio || "—")} a ${esc(item.janelaProventosFim || "—")}.
        Cotação usada: ${esc(item.preco || "—")} — fechamento B3 de ${esc(item.dataCotacao || "—")}.
      </div>

      <div class="quality-note"><b>Conclusão do filtro:</b> ${esc(item.motivoQualidade)}</div>
      <div class="section-title">Checklist Anti-Lixo</div>
      ${criteriaHtml(item.criterios)}

      <details class="dividend-events">
        <summary>Ver proventos considerados no DPA 12M</summary>
        ${dividendsHtml(item.eventosProventos)}
      </details>

      <div class="sources">
        <span><b>CNPJ:</b> ${esc(item.cnpj)}</span>
        <span><b>Código CVM:</b> ${esc(item.codigoCvm)}</span>
        <span><b>Fundamentos:</b> ${esc(item.origemFundamentos)}</span>
        <span><b>Fonte mercado:</b> ${esc(item.fonteCotacao)}</span>
        <span><b>Fonte fundamentos:</b> ${esc(item.fonteFundamentos)}</span>
        <span><b>Fonte proventos:</b> ${esc(item.fonteProventos || "B3 Companhias Listadas")}</span>
      </div>
    </div>
  </td></tr>`;
}

function filteredRows() {
  const query = document.getElementById("q")?.value.trim().toUpperCase() || "";
  const filter = document.getElementById("filter")?.value || "";
  const bazinFilter = document.getElementById("bazinFilter")?.value || "";
  const sort = document.getElementById("sort")?.value || "liquidity";
  let rows = DATA.filter(item => {
    const matchesQuery = !query || `${item.ticker} ${item.empresa}`.toUpperCase().includes(query);
    let matchesFilter = true;
    if (filter === "INITIAL") matchesFilter = item.elegivelInicial === "SIM";
    if (filter === "QUALITY") matchesFilter = item.elegivelInicial === "SIM" && item.filtroQualidadeOriginal === "APROVADA NO FILTRO";
    if (filter === "RED") matchesFilter = item.elegivelInicial === "SIM" && item.filtroQualidadeOriginal === "ALERTA VERMELHO";
    if (filter === "PENDING") matchesFilter = item.elegivelInicial === "SIM" && (
      String(item.filtroQualidadeOriginal || "").startsWith("PENDENTE") ||
      String(item.valuationStatus || "").startsWith("BLOQUEADO")
    );
    if (filter === "OUT") matchesFilter = item.elegivelInicial !== "SIM";

    const valuation = String(item.valuationStatus || "");
    const bazinStatus = String(item.statusBazin || "");
    let matchesBazin = true;
    if (bazinFilter === "RELEASED") matchesBazin = valuation.startsWith("LIBERADO");
    if (bazinFilter === "ATTRACTIVE") matchesBazin = bazinStatus.startsWith("ATRATIVA");
    if (bazinFilter === "NEUTRAL") matchesBazin = bazinStatus.startsWith("NEUTRA");
    if (bazinFilter === "EXPENSIVE") matchesBazin = bazinStatus.startsWith("CARA");
    if (bazinFilter === "BLOCKED") matchesBazin = valuation.startsWith("BLOQUEADO");
    if (bazinFilter === "PENDING") matchesBazin = !valuation.startsWith("BLOQUEADO") && !valuation.startsWith("LIBERADO");

    return matchesQuery && matchesFilter && matchesBazin;
  });
  rows = [...rows].sort((a, b) => {
    if (sort === "ticker") return a.ticker.localeCompare(b.ticker);
    if (sort === "quality") return (b.scoreQualidade ?? -1) - (a.scoreQualidade ?? -1);
    if (sort === "roe") return (b.roe5aRaw ?? -999) - (a.roe5aRaw ?? -999);
    if (sort === "dy") return (b.dy12mRaw ?? -999) - (a.dy12mRaw ?? -999);
    if (sort === "bazin") return (b.margemBazinRaw ?? -999) - (a.margemBazinRaw ?? -999);
    return (b.volume20Raw ?? 0) - (a.volume20Raw ?? 0);
  });
  return rows;
}

function render() {
  const body = document.getElementById("body");
  if (!body) return;
  const rows = filteredRows();
  body.innerHTML = rows.map(item => {
    const bazinStatus = bazinDisplayStatus(item);
    const bazinDetail = bazinStatus === "BLOQUEADO"
      ? item.valuationStatus
      : (item.margemBazin || item.valuationStatus || "—");
    return `
    <tr class="main-row" data-ticker="${esc(item.ticker)}" tabindex="0">
      <td><strong>${esc(item.ticker)}</strong><small>${esc(item.segmento)}</small></td>
      <td>${esc(item.empresa)}</td>
      <td>${esc(item.preco)}<small>${esc(item.variacao)}</small></td>
      <td><span class="pill ${badgeClass(item.elegivelInicial)}">${item.elegivelInicial === "SIM" ? "OK" : "NÃO"}</span><small>${esc(item.motivoInicial)}</small></td>
      <td><span class="pill ${badgeClass(item.filtroQualidade)}">${esc(item.filtroQualidade)}</span>${item.scoreQualidade !== null ? `<small>Score ${esc(item.scoreQualidade)}/100</small>` : ""}</td>
      <td>${esc(item.roe5a)}</td>
      <td>${esc(item.cagrLucro5a)}</td>
      <td>${esc(item.dlEbitda)}</td>
      <td>${esc(item.margemLiquida)}<small>${esc(item.tendenciaMargem)}</small></td>
      <td>${esc(item.dy12m || "—")}<small>DPA ${esc(item.dpa12m || "Pendente")}</small></td>
      <td><span class="pill ${badgeClass(bazinStatus)}">${esc(bazinStatus)}</span><small>${esc(bazinDetail)}</small></td>
    </tr>${detailsRow(item)}`;
  }).join("");

  document.querySelectorAll(".details-row").forEach(row => row.hidden = true);
  document.querySelectorAll(".main-row").forEach(row => {
    const toggle = () => {
      const details = document.querySelector(`[data-details="${CSS.escape(row.dataset.ticker)}"]`);
      if (!details) return;
      const opening = details.hidden;
      document.querySelectorAll(".details-row:not([hidden])").forEach(openRow => {
        openRow.hidden = true;
        openRow.previousElementSibling?.classList.remove("open");
      });
      details.hidden = !opening;
      row.classList.toggle("open", opening);
      if (opening) row.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      }
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
    setText("qualityApproved", payload.qualityApproved ?? 0);
    setText("redAlerts", payload.redAlerts ?? 0);
    setText("bazinEnabled", payload.bazinEnabled ?? 0);
    setText("quoteDate", payload.latestQuoteDate || "-");
    const dividendStamp = payload.dividendsUpdatedAt ? ` | Proventos ${formatGeneratedAt(payload.dividendsUpdatedAt)}` : "";
    setText("updated", `Gerado em ${formatGeneratedAt(payload.generatedAt)}${dividendStamp}`);
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

["q", "filter", "bazinFilter", "sort"].forEach(id => {
  const element = document.getElementById(id);
  if (element) element.addEventListener(id === "q" ? "input" : "change", render);
});
load();
