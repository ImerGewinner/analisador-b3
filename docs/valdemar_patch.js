function analyzePortfolio(){
  const positions=parsePortfolio();
  localStorage.setItem('valdemarPortfolio',document.getElementById('portfolioInput').value);
  if(!positions.length){document.getElementById('portfolioResult').innerHTML='<p>Nenhuma posição válida.</p>';return;}
  const total=positions.reduce((sum,position)=>sum+position.value,0);
  const sectors={};
  let roeSum=0,roeCoverage=0,dySum=0,dyCoverage=0;
  positions.forEach(position=>{
    position.weight=position.value/total;
    sectors[position.item.segmento]=(sectors[position.item.segmento]||0)+position.weight;
    if(position.item.roe5aRaw!=null){roeSum+=position.weight*position.item.roe5aRaw;roeCoverage+=position.weight;}
    if(position.item.dy12mRaw!=null){dySum+=position.weight*position.item.dy12mRaw;dyCoverage+=position.weight;}
  });
  const weightedRoe=roeCoverage?roeSum/roeCoverage:null;
  const weightedDy=dyCoverage?dySum/dyCoverage:null;
  const hhi=positions.reduce((sum,position)=>sum+(position.weight*100)**2,0);
  const risks=[];
  positions.filter(position=>position.weight>.20).forEach(position=>risks.push(`Posição ${position.ticker} representa ${(position.weight*100).toFixed(1)}%, acima do alerta de 20%.`));
  Object.entries(sectors).filter(([,weight])=>weight>.35).forEach(([sector,weight])=>risks.push(`Setor ${sector} representa ${(weight*100).toFixed(1)}%, acima do alerta de 35%.`));
  positions.filter(position=>position.item.filtroQualidadeOriginal==='ALERTA VERMELHO').forEach(position=>risks.push(`${position.ticker} está em Alerta Vermelho no filtro fundamentalista.`));
  document.getElementById('portfolioResult').innerHTML=`
    <div class="metric-cards">
      <div class="metric-card"><span>Patrimônio informado</span><strong>${money(total)}</strong></div>
      <div class="metric-card"><span>HHI simplificado</span><strong>${hhi.toFixed(0)}</strong></div>
      <div class="metric-card"><span>ROE ponderado</span><strong>${weightedRoe==null?'Pendente':pct(weightedRoe)}</strong><small>Cobertura ${pct(roeCoverage)}</small></div>
      <div class="metric-card"><span>DY ponderado</span><strong>${weightedDy==null?'Pendente':pct(weightedDy)}</strong><small>Cobertura ${pct(dyCoverage)}</small></div>
    </div>
    <div class="table-scroll"><table class="portfolio-table"><thead><tr><th>Ticker</th><th>Valor</th><th>Peso</th><th>Setor</th><th>Qualidade</th><th>Bazin</th><th>DCF</th></tr></thead><tbody>${positions.map(position=>`<tr><td>${position.ticker}</td><td>${money(position.value)}</td><td>${pct(position.weight)}</td><td>${esc(position.item.segmento)}</td><td>${tag(position.item.filtroQualidadeOriginal)}</td><td>${esc(position.item.margemBazin||'—')}</td><td>${esc(position.item.margemDcf||'—')}</td></tr>`).join('')}</tbody></table></div>
    <h3>Riscos</h3>${risks.length?`<ul class="risk-list">${risks.map(risk=>`<li>${esc(risk)}</li>`).join('')}</ul>`:'<p>Nenhum alerta mecânico de concentração ou qualidade.</p>'}
    <p><b>Leitura educacional:</b> indicadores agregados usam apenas posições com dados disponíveis; a cobertura é informada para evitar que ausências sejam tratadas como zero.</p>`;
}

function dividendProjection(){
  const item=byTicker()[document.getElementById('projectionTicker').value];
  const shares=Number(document.getElementById('projectionShares').value);
  if(!item||!(shares>0)||item.dpa12mRaw==null){document.getElementById('dividendProjectionResult').innerHTML='<p>Selecione uma ação com DPA 12M validado e informe a quantidade.</p>';return;}
  if(item.cagrDpa5ARaw==null){document.getElementById('dividendProjectionResult').innerHTML='<p>Projeção bloqueada: histórico completo de DPA por cinco anos não foi conciliado para esta classe de ação.</p>';return;}
  const historical=Number(item.cagrDpa5ARaw);
  const scenarios=[['Conservador',historical*.5],['Base',Math.min(historical,.06)],['Otimista',Math.min(historical*1.25,.10)]];
  const years=[5,10];
  document.getElementById('dividendProjectionResult').innerHTML=`<div class="table-scroll"><table class="projection-table"><thead><tr><th>Cenário</th><th>Crescimento</th>${years.map(year=>`<th>DPA ${year}A</th><th>Renda anual ${year}A</th>`).join('')}</tr></thead><tbody>${scenarios.map(([name,growth])=>`<tr><td>${name}</td><td>${pct(growth)}</td>${years.map(year=>{const dpa=item.dpa12mRaw*Math.pow(1+growth,year);return `<td>${money(dpa)}</td><td>${money(dpa*shares)}</td>`}).join('')}</tr>`).join('')}</tbody></table></div><p>Base: DPA 12M ${item.dpa12m}; CAGR histórico ${item.cagrDpa5A}. Projeção meramente ilustrativa.</p>`;
}

document.getElementById('portfolioButton').onclick=analyzePortfolio;
document.getElementById('dividendProjectionButton').onclick=dividendProjection;
