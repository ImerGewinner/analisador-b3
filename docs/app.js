let DATA = [];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function render() {
  const query = document.getElementById("q").value.trim().toUpperCase();
  const filter = document.getElementById("filter").value;
  const rows = DATA.filter(item => {
    const matchesQuery = !query || `${item.ticker} ${item.empresa}`.toUpperCase().includes(query);
    const matchesFilter = !filter || item.elegivel === filter;
    return matchesQuery && matchesFilter;
  });

  document.getElementById("body").innerHTML = rows.map(item => `
    <tr>
      <td><strong>${esc(item.ticker)}</strong></td>
      <td>${esc(item.empresa)}</td>
      <td>${esc(item.segmento)}</td>
      <td>${esc(item.preco)}</td>
      <td>${esc(item.variacao)}</td>
      <td>${esc(item.volume20)}</td>
      <td>${esc(item.pregoes20)}</td>
      <td><span class="pill ${item.elegivel === "SIM" ? "ok" : "no"}">${esc(item.elegivel)}</span></td>
      <td>${esc(item.motivo)}</td>
      <td>${esc(item.fonte)}</td>
    </tr>
  `).join("");
}

async function load() {
  try {
    const response = await fetch(`data.json?t=${Date.now()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    DATA = payload.items || [];
    document.getElementById("count").textContent = payload.count ?? 0;
    document.getElementById("eligible").textContent = payload.eligible ?? 0;
    document.getElementById("quoteDate").textContent = payload.latestQuoteDate || "-";
    document.getElementById("updated").textContent = `Gerado em ${payload.generatedAt || "-"}`;
    document.getElementById("disclaimer").textContent = payload.disclaimer || "";
    render();
  } catch (error) {
    document.getElementById("updated").textContent = `Erro ao carregar dados: ${error.message}`;
  }
}

document.getElementById("q").addEventListener("input", render);
document.getElementById("filter").addEventListener("change", render);
load();
