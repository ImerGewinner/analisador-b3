let DATA = [];

const APPROVED = "APROVADA NO FILTRO";
const NOT_CLASSIFIED = "Não classificada — filtro não aprovado";
const esc = value => String(value ?? "").replace(
  /[&<>"']/g,
  char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]),
);
const setText = (id, value) => {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
};
const money = value => value == null
  ? "—"
  : new Intl.NumberFormat("pt-BR", {style: "currency", currency: "BRL"}).format(Number(value));
const pct = value => {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const number = Number(value) * 100;
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(2).replace(".", ",")}%`;
};

function badgeClass(status) {
  const text = String(status || "").toUpperCase();
  if (
    text === "SIM" || text === "OK" || text === "APROVADO" ||
    text === APPROVED || text.startsWith("CALCULADO") || text.startsWith("MARGEM ≥")
  ) return "ok";
  if (
    text.includes("ALERTA VERMELHO") || text.includes("REPROVADA") ||
    text === "REPROVADO" || text.includes("MARGEM NEGATIVA")
  ) return "danger";
  if (
    text.includes("BLOQUEADO") || text.includes("NÃO APLICÁVEL") ||
    text === "NÃO" || text.includes("FORA DO UNIVERSO")
  ) return "no";
  return "pending";
}

function pill(status, label = status) {
  return `<span class="pill ${badgeClass(status)}">${esc(label || "—")}</span>`;
}

function bazinDisplay(item) {
  if (item.precoTetoBazinRaw != null) return item.statusBazin || "CALCULADO";
  if (String(item.valuationStatus || "").includes("NÃO APLICÁVEL")) return "NÃO APLICÁVEL";
  return "BLOQUEADO";
}

function dcfDisplay(item) {
  const status = String(item.statusDcf || "PENDENTE");
  if (item.precoJustoDcfRaw != null) return item.margemDcf || "CALCULADO";
  if (status.includes("NÃO APLICÁVEL")) return "NÃO APLICÁVEL";
  if (status.includes("bloqueado") || status.includes("BLOQUEADO")) return "BLOQUEADO";
  return "PENDENTE";
}

function criteriaHtml(criteria) {
  if (!criteria?.length) return '<p class="muted-text">Critérios não disponíveis.</p>';
  return `<div class="criteria">${criteria.map(criterion => `
    <article class="criterion">
      <div>
        <strong>${esc(criterion.name)}</strong>
        <small>Limite: ${esc(criterion.limit)}</small>
      </div>
      <div class="criterion-value">${esc(criterion.value)}</div>
      ${pill(criterion.status)}
      ${criterion.note ? `<p>${esc(criterion.note)}</p>` : ""}
    </article>
  `).join("")}</div>`;
}

function dividendsHtml(events) {
  if (!events?.length) return '<p class="empty-state">Nenhum evento elegível na janela ou fonte pendente.</p>';
  return `<div class="events-table-wrap"><table class="events-table">
    <thead><tr><th>Tipo</th><th>Data COM</th><th>Pagamento</th><th>Valor/ação</th><th>Tratamento</th></tr></thead>
    <tbody>${events.map(event => `<tr>
      <td>${esc(event.tipo)}</td>
      <td>${esc(event.dataCom || "—")}</td>
      <td>${esc(event.pagamento || "—")}</td>
      <td>${esc(event.valor)}</td>
      <td>${event.extraordinarioExplicito ? "Separado como extraordinário" : "Incluído no DPA normalizado"}</td>
    </tr>`).join("")}</tbody>
  </table></div>`;
}

function financialBlock(item) {
  if (!item.financeira) return "";
  return `<div class="section-title">Métricas regulatórias</div>
    <div class="fund-grid advanced-grid">
      <div><span>Status da fonte</span><strong>${esc(item.ifdataStatus || "Pendente")}</strong><small>${esc(item.ifdataInstituicao || "")} ${item.ifdataPeriodo ? `| ${esc(item.ifdataPeriodo)}` : ""}</small></div>
      <div><span>Índice de Basileia</span><strong>${esc(item.basileia || "—")}</strong><small>Mínimo de referência: 10,50%</small></div>
      <div><span>Capital principal / Nível I</span><strong>${esc(item.capitalPrincipal || "—")} / ${esc(item.nivel1 || "—")}</strong></div>
      <div><span>Eficiência</span><strong>${esc(item.eficiencia || "—")}</strong><small>Limite metodológico: 60,00%</small></div>
      <div><span>Inadimplência &gt; 90 dias</span><strong>${esc(item.inadimplencia || "—")}</strong><small>Limite metodológico: 5,00%</small></div>
      <div><span>Cobertura</span><strong>${esc(item.cobertura || "—")}</strong><small>Limite metodológico: 100,00%</small></div>
    </div>`;
}

function sectorContext(item) {
  const segment = String(item.segmento || "").toUpperCase();
  if (segment.includes("INCORP")) {
    return "Incorporação é um negócio cíclico, sensível à Selic, ao crédito imobiliário, aos custos de obra e à velocidade de vendas. O P/L baixo, isoladamente, não elimina esses riscos.";
  }
  if (item.financeira) {
    return "Instituições financeiras exigem leitura de capital regulatório, eficiência e risco de crédito. Dívida Líquida/EBITDA e DCF industrial não são usados.";
  }
  if (segment.includes("ENERG")) {
    return "O setor combina previsibilidade contratual com risco regulatório, necessidade de capital e sensibilidade a juros. Compare dívida, retorno e qualidade das concessões.";
  }
  if (segment.includes("VAREJO") || segment.includes("COMÉRCIO")) {
    return "O setor tende a responder a renda, crédito, juros, competição e giro de estoques. Margens e dívida merecem leitura conjunta.";
  }
  return "O contexto setorial deve ser lido junto com ciclo econômico, estrutura de capital e comparação entre pares; nenhum múltiplo isolado determina uma decisão.";
}

function analysisSummary(item) {
  const status = item.filtroQualidadeOriginal || "PENDENTE";
  const failed = (item.criterios || []).filter(c => c.essential !== false && c.status === "REPROVADO");
  const pending = (item.criterios || []).filter(c => c.essential !== false && !["APROVADO", "REPROVADO"].includes(c.status));
  if (status === APPROVED) {
    return `A empresa passou nos critérios essenciais usados pelo site. Isso apenas libera a etapa matemática; não significa aprovação para compra.`;
  }
  const causes = failed.length
    ? failed.map(c => `${c.name}: ${c.value} (limite ${c.limit})`).join("; ")
    : pending.map(c => `${c.name}: dado pendente`).join("; ");
  return `${status}. O fluxo termina antes do valuation${causes ? ` porque ${causes}` : ""}.`;
}

function manualQuoteHtml(item) {
  return `<div class="manual-quote">
    <div>
      <strong>Usar uma cotação informada por você</strong>
      <p>O fechamento B3 acima permanece como referência histórica. Este cálculo não altera nem salva a base.</p>
    </div>
    <form data-manual-quote="${esc(item.ticker)}">
      <input type="number" min="0.01" step="0.01" inputmode="decimal" aria-label="Cotação manual de ${esc(item.ticker)}" placeholder="Ex.: 4,15">
      <button type="submit" class="quote-button">Recalcular margens</button>
    </form>
    <div class="manual-result" data-manual-result="${esc(item.ticker)}"></div>
  </div>`;
}

function detailsRow(item) {
  const liquidity = item.elegivelInicial === "SIM" ? "OK" : "REPROVADA";
  const history = item.anosDividendos5A == null
    ? "Pendente"
    : `${item.anosDividendos5A}/5`;
  const classification = item.filtroQualidadeOriginal === APPROVED
    ? (item.classificacao3P || "Classificação pendente")
    : NOT_CLASSIFIED;
  return `<tr class="details-row" data-details="${esc(item.ticker)}"><td colspan="12"><div class="details-panel">
    <div class="analysis-lead">
      <div><span>Resultado determinístico</span>${pill(item.filtroQualidadeOriginal)}</div>
      <div><span>Classificação 3 pilares</span><strong>${esc(classification)}</strong></div>
      <p>${esc(analysisSummary(item))}</p>
    </div>
    <div class="status-strip status-strip-four">
      <div class="status-step"><span>Etapa 1</span><strong>Liquidez</strong>${pill(liquidity)}</div>
      <div class="status-step"><span>Etapa 2</span><strong>Qualidade</strong>${pill(item.filtroQualidadeOriginal)}</div>
      <div class="status-step"><span>Etapa 3</span><strong>Bazin</strong>${pill(bazinDisplay(item))}</div>
      <div class="status-step"><span>Etapa 4</span><strong>DCF</strong>${pill(item.statusDcf, dcfDisplay(item))}</div>
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

    ${financialBlock(item)}

    <div class="section-title">Consistência, setor e macro</div>
    <div class="context-box"><strong>${esc(item.segmento || "Setor não identificado")}</strong><p>${esc(sectorContext(item))}</p></div>
    <div class="fund-grid advanced-grid">
      <div><span>P/L</span><strong>${esc(item.pl || "—")}</strong><small>Mediana do setor: ${esc(item.plSetor || "—")} | ${esc(item.statusPlSetor || "")}</small></div>
      <div><span>Selic / spread DY</span><strong>${esc(item.selic || "—")}</strong><small>Spread corrente: ${esc(item.spreadDySelic || "—")} — riscos não equivalentes</small></div>
      <div><span>Dividendos em 5 anos</span><strong>${esc(history)}</strong><small>Sequência ${item.sequenciaDividendos5A ?? "Pendente"} | CAGR DPA ${esc(item.cagrDpa5A || "—")}</small></div>
      <div><span>Diluição estimada 5A</span><strong>${esc(item.diluicao5A || "—")}</strong><small>${esc(item.statusDiluicao || "PENDENTE")}</small></div>
    </div>

    <div class="section-title">Proventos e integridade</div>
    <div class="fund-grid advanced-grid">
      <div><span>DPA normalizado / DY</span><strong>${esc(item.dpa12m || "—")} / ${esc(item.dy12m || "—")}</strong><small>Payout ${esc(item.payout || "—")}</small></div>
      <div><span>DPA total 12M</span><strong>${esc(item.dpaTotal12m || item.dpa12m || "—")}</strong><small>Extraordinário separado: ${esc(item.dpaExtraordinario12m || "R$ 0,0000")}</small></div>
      <div><span>Mediana histórica anual</span><strong>${esc(item.medianaDpaHistorico || "Pendente")}</strong></div>
      <div><span>Integridade dos proventos</span><strong>${esc(item.integridadeProventos || "Pendente")}</strong></div>
    </div>

    <div class="section-title">Valuation</div>
    <div class="valuation-gate ${item.filtroQualidadeOriginal === APPROVED ? "released" : "blocked"}">
      <strong>${esc(item.valuationStatus || "Valuation bloqueado pelo filtro de qualidade.")}</strong>
      <p>${item.filtroQualidadeOriginal === APPROVED ? "As margens abaixo são resultados matemáticos sobre o último fechamento B3." : "Preço baixo não substitui qualidade nem dados essenciais."}</p>
    </div>
    <div class="fund-grid advanced-grid">
      <div><span>Referência Bazin</span><strong>${esc(item.precoTetoBazin || "Bloqueado")}</strong><small>Margem ${esc(item.margemBazin || "—")} | ${esc(item.statusBazin || "BLOQUEADO")}</small></div>
      <div><span>Valor intrínseco DCF</span><strong>${esc(item.valorIntrinsecoDcf || "—")}</strong><small>Antes da margem de segurança</small></div>
      <div><span>Referência DCF com 25%</span><strong>${esc(item.precoJustoDcf || "—")}</strong><small>Margem ${esc(item.margemDcf || "—")}</small></div>
      <div><span>FCFF médio / CAGR 3A</span><strong>${esc(item.fcfMedio3A || "—")}</strong><small>CAGR ${esc(item.cagrFcf3A || "—")} | g usado ${esc(item.crescimentoDcf || "—")}</small></div>
    </div>

    ${manualQuoteHtml(item)}
    <div class="quality-note"><b>Conclusão do filtro:</b> ${esc(item.motivoQualidade || "Pendente")}</div>
    <div class="section-title">Checklist de qualidade</div>
    ${criteriaHtml(item.criterios)}
    <details class="dividend-events"><summary>Ver proventos considerados no DPA 12M</summary>${dividendsHtml(item.eventosProventos)}</details>
    <div class="sources">
      <span><b>CNPJ:</b> ${esc(item.cnpj)}</span>
      <span><b>Código CVM:</b> ${esc(item.codigoCvm)}</span>
      <span><b>Preço:</b> último fechamento B3 em ${esc(item.dataCotacao)} — ${esc(item.preco)}</span>
      <span><b>Fundamentos:</b> ${esc(item.fonteFundamentos)}</span>
      <span><b>Proventos:</b> ${esc(item.fonteProventos || "B3")}</span>
      <span><b>Macro/FCFF:</b> ${esc(item.fonteAvancada || "CVM/BCB")}</span>
      ${item.fonteRegulatoria ? `<span><b>Regulatório:</b> ${esc(item.fonteRegulatoria)}</span>` : ""}
    </div>
  </div></td></tr>`;
}

function filteredRows() {
  const query = document.getElementById("q")?.value.trim().toUpperCase() || "";
  const general = document.getElementById("filter")?.value || "";
  const bazin = document.getElementById("bazinFilter")?.value || "";
  const dcf = document.getElementById("dcfFilter")?.value || "";
  const sort = document.getElementById("sort")?.value || "liquidity";

  const rows = DATA.filter(item => {
    const matchesQuery = !query || `${item.ticker} ${item.empresa}`.toUpperCase().includes(query);
    const quality = item.filtroQualidadeOriginal || "";
    let matchesGeneral = true;
    if (general === "INITIAL") matchesGeneral = item.elegivelInicial === "SIM";
    if (general === "QUALITY") matchesGeneral = item.elegivelInicial === "SIM" && quality === APPROVED;
    if (general === "REJECTED") matchesGeneral = quality === "REPROVADA NO FILTRO";
    if (general === "RED") matchesGeneral = quality === "ALERTA VERMELHO";
    if (general === "PENDING") matchesGeneral = quality.startsWith("PENDENTE");
    if (general === "OUT") matchesGeneral = item.elegivelInicial !== "SIM";

    const bazinMargin = item.margemBazinRaw;
    let matchesBazin = true;
    if (bazin === "RELEASED") matchesBazin = item.precoTetoBazinRaw != null;
    if (bazin === "GE10") matchesBazin = bazinMargin != null && bazinMargin >= 0.10;
    if (bazin === "ZERO10") matchesBazin = bazinMargin != null && bazinMargin >= 0 && bazinMargin < 0.10;
    if (bazin === "NEGATIVE") matchesBazin = bazinMargin != null && bazinMargin < 0;
    if (bazin === "BLOCKED") matchesBazin = item.precoTetoBazinRaw == null && !String(item.valuationStatus || "").includes("NÃO APLICÁVEL");
    if (bazin === "NOT_APPLICABLE") matchesBazin = String(item.valuationStatus || "").includes("NÃO APLICÁVEL");

    const dcfMargin = item.margemDcfRaw;
    const dcfStatus = String(item.statusDcf || "");
    let matchesDcf = true;
    if (dcf === "CALCULATED") matchesDcf = item.precoJustoDcfRaw != null;
    if (dcf === "GE10") matchesDcf = dcfMargin != null && dcfMargin >= 0.10;
    if (dcf === "NEGATIVE") matchesDcf = dcfMargin != null && dcfMargin < 0;
    if (dcf === "BLOCKED") matchesDcf = dcfStatus.toUpperCase().includes("BLOQUEADO");
    if (dcf === "PENDING") matchesDcf = dcfStatus.startsWith("PENDENTE");
    if (dcf === "NOT_APPLICABLE") matchesDcf = dcfStatus.includes("NÃO APLICÁVEL");
    return matchesQuery && matchesGeneral && matchesBazin && matchesDcf;
  });

  return [...rows].sort((a, b) => {
    if (sort === "ticker") return a.ticker.localeCompare(b.ticker);
    if (sort === "quality") return (b.scoreQualidade ?? -1) - (a.scoreQualidade ?? -1);
    if (sort === "roe") return (b.roe5aRaw ?? -999) - (a.roe5aRaw ?? -999);
    if (sort === "dy") return (b.dy12mRaw ?? -999) - (a.dy12mRaw ?? -999);
    if (sort === "bazin") return (b.margemBazinRaw ?? -999) - (a.margemBazinRaw ?? -999);
    if (sort === "dcf") return (b.margemDcfRaw ?? -999) - (a.margemDcfRaw ?? -999);
    if (sort === "pe") return (a.plRaw ?? 999) - (b.plRaw ?? 999);
    if (sort === "dividends") return (b.anosDividendos5A ?? -1) - (a.anosDividendos5A ?? -1);
    return (b.volume20Raw ?? 0) - (a.volume20Raw ?? 0);
  });
}

function bindManualQuotes() {
  document.querySelectorAll("[data-manual-quote]").forEach(form => {
    form.addEventListener("submit", event => {
      event.preventDefault();
      const ticker = form.dataset.manualQuote;
      const item = DATA.find(row => row.ticker === ticker);
      const input = form.querySelector("input");
      const price = Number(input.value);
      const target = document.querySelector(`[data-manual-result="${CSS.escape(ticker)}"]`);
      if (!target) return;
      if (!(price > 0)) {
        target.innerHTML = "<p>Informe uma cotação válida.</p>";
        return;
      }
      if (!item || item.filtroQualidadeOriginal !== APPROVED) {
        target.innerHTML = `<p><b>Cotação fornecida pelo utilizador: ${money(price)}.</b> Valuation bloqueado pelo filtro de qualidade.</p>`;
        return;
      }
      const bazinMargin = item.precoTetoBazinRaw == null ? null : item.precoTetoBazinRaw / price - 1;
      const dcfMargin = item.precoJustoDcfRaw == null ? null : item.precoJustoDcfRaw / price - 1;
      target.innerHTML = `<p><b>Cotação fornecida pelo utilizador: ${money(price)}.</b> Margem Bazin: ${pct(bazinMargin)}. Margem DCF: ${pct(dcfMargin)}. Isso não representa sinal de compra.</p>`;
    });
  });
}

function render() {
  const body = document.getElementById("body");
  if (!body) return;
  body.innerHTML = filteredRows().map(item => `
    <tr class="main-row" data-ticker="${esc(item.ticker)}" tabindex="0">
      <td><strong>${esc(item.ticker)}</strong><small>${esc(item.segmento)}</small></td>
      <td>${esc(item.empresa)}</td>
      <td>${esc(item.preco)}<small>Fechamento ${esc(item.dataCotacao)} | ${esc(item.variacao)}</small></td>
      <td>${pill(item.elegivelInicial, item.elegivelInicial === "SIM" ? "OK" : "NÃO")}<small>${esc(item.motivoInicial)}</small></td>
      <td>${pill(item.filtroQualidadeOriginal)}<small>${item.falhas ?? 0} reprovado(s) | ${item.pendencias ?? 0} pendente(s)</small></td>
      <td>${esc(item.roe5a)}</td>
      <td>${esc(item.cagrLucro5a)}</td>
      <td>${esc(item.dlEbitda)}</td>
      <td>${esc(item.margemLiquida)}<small>${esc(item.tendenciaMargem)}</small></td>
      <td>${esc(item.dy12m || "—")}<small>DPA normalizado ${esc(item.dpa12m || "—")}</small></td>
      <td>${pill(bazinDisplay(item))}<small>${esc(item.margemBazin || item.valuationStatus || "—")}</small></td>
      <td>${pill(item.statusDcf, dcfDisplay(item))}<small>${esc(item.precoJustoDcf || item.statusDcf || "—")}</small></td>
    </tr>
    ${detailsRow(item)}
  `).join("");

  document.querySelectorAll(".details-row").forEach(row => { row.hidden = true; });
  document.querySelectorAll(".main-row").forEach(row => {
    const toggle = () => {
      const details = document.querySelector(`[data-details="${CSS.escape(row.dataset.ticker)}"]`);
      if (!details) return;
      const opening = details.hidden;
      document.querySelectorAll(".details-row:not([hidden])").forEach(open => {
        open.hidden = true;
        open.previousElementSibling?.classList.remove("open");
      });
      details.hidden = !opening;
      row.classList.toggle("open", opening);
      if (opening) row.scrollIntoView({behavior: "smooth", block: "nearest"});
    };
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      }
    });
  });
  bindManualQuotes();
}

function formatDate(value) {
  if (!value) return "—";
  try {
    return new Intl.DateTimeFormat("pt-BR", {
      timeZone: "America/Manaus",
      dateStyle: "short",
      timeStyle: "short",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

async function load() {
  try {
    const response = await fetch(`data.json?t=${Date.now()}`, {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    DATA = payload.items || [];
    setText("count", payload.count ?? 0);
    setText("initialEligible", payload.initialEligible ?? 0);
    setText("qualityApproved", payload.qualityApproved ?? 0);
    setText("qualityRejected", payload.qualityRejected ?? 0);
    setText("redAlerts", payload.redAlerts ?? 0);
    setText("bazinEnabled", payload.bazinEnabled ?? 0);
    setText("dcfEnabled", payload.dcfEnabled ?? 0);
    setText("selic", payload.macro?.selic || "Pendente");
    setText("quoteDate", payload.latestQuoteDate || "—");
    setText("updated", `Gerado ${formatDate(payload.generatedAt)} | Proventos ${formatDate(payload.dividendsUpdatedAt)} | Avançado ${formatDate(payload.advancedUpdatedAt)}`);
    setText("disclaimer", payload.disclaimer || "");
    const methodology = payload.methodology || {};
    setText("methodInitial", methodology.initial || "");
    setText("methodQuality", methodology.quality || "");
    setText("methodFinancial", methodology.financial || "");
    setText("methodValuation", methodology.valuation || "");
    setText("methodDcf", methodology.dcf || "");
    setText("methodMacro", methodology.macro || "");
    setText("methodConsistency", methodology.consistency || "");

    const ticker = new URLSearchParams(window.location.search).get("ticker")?.toUpperCase();
    if (ticker && DATA.some(item => item.ticker === ticker)) {
      document.getElementById("q").value = ticker;
      document.getElementById("filter").value = "";
    }
    render();
    if (ticker) document.querySelector(`[data-ticker="${CSS.escape(ticker)}"]`)?.click();
  } catch (error) {
    setText("updated", `Erro: ${error.message}`);
  }
}

["q", "filter", "bazinFilter", "dcfFilter", "sort"].forEach(id => {
  const element = document.getElementById(id);
  if (element) element.addEventListener(id === "q" ? "input" : "change", render);
});

load();
