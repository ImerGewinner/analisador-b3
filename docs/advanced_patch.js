const _advancedDetailsRow=detailsRow;
detailsRow=function(item){
  let html=_advancedDetailsRow(item);
  if(item.anosDividendos5A==null){
    html=html.replace(
      '<span>Dividendos 5A</span><strong>0/5</strong><small>Sequência 0 | CAGR DPA —</small>',
      '<span>Dividendos 5A</span><strong>Pendente</strong><small>Histórico completo por classe não conciliado</small>'
    );
  }
  return html;
};
