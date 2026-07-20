const _baseDetailsRow=detailsRow;
detailsRow=function(item){
  let html=_baseDetailsRow(item);
  if(!item.financeira)return html;
  const block=`<div class="section-title">Métricas regulatórias</div><div class="fund-grid advanced-grid">
    <div><span>Status regulatório</span><strong>${esc(item.ifdataStatus||'Pendente')}</strong><small>${esc(item.ifdataInstituicao||'')} ${item.ifdataPeriodo?`| ${esc(item.ifdataPeriodo)}`:''}</small></div>
    <div><span>Índice de Basileia</span><strong>${esc(item.basileia||'—')}</strong><small>IFData/BCB</small></div>
    <div><span>Capital principal / Nível I</span><strong>${esc(item.capitalPrincipal||'—')} / ${esc(item.nivel1||'—')}</strong></div>
    <div><span>Eficiência</span><strong>${esc(item.eficiencia||'—')}</strong></div>
    <div><span>Inadimplência</span><strong>${esc(item.inadimplencia||'—')}</strong></div>
    <div><span>Cobertura</span><strong>${esc(item.cobertura||'—')}</strong></div>
  </div>`;
  html=html.replace('<div class="classification-box">',`${block}<div class="classification-box">`);
  if(item.fonteRegulatoria){
    html=html.replace('</div></div></td></tr>',`<span><b>Regulatório:</b> ${esc(item.fonteRegulatoria)}</span></div></div></td></tr>`);
  }
  return html;
};
