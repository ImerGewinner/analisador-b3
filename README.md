# Analisador B3 — Valdemar

Projeto educacional em Python, SQLite, GitHub Actions e GitHub Pages para analisar ações brasileiras com dados públicos e auditáveis.

## Fluxo implementado

1. **Mercado B3:** fechamento oficial COTAHIST, volume, frequência de negociação e seleção da classe principal por emissor.
2. **Fundamentos CVM:** DFP dos últimos exercícios e ITR atual/comparável para receita, lucro, EBITDA, patrimônio, caixa e dívida.
3. **Checklist Anti-Lixo:** ROE médio 5A, CAGR do lucro, dívida líquida/EBITDA, tendência da margem, lucros positivos e payout.
4. **Proventos:** dividendos e JCP por classe, DPA 12M, dividend yield, payout e histórico de cinco anos.
5. **Bazin:** preço-teto `DPA 12M / 7,75%` e margem sobre o fechamento B3 validado.
6. **DCF simplificado:** FCF médio 3A, crescimento limitado a 6%, WACC de 12% e desconto de segurança de 25%.
7. **Contexto:** P/L contra mediana do setor, Meta Selic oficial do Banco Central, spread DY–Selic, consistência de dividendos e diluição estimada.
8. **Financeiras:** integração IFData para Basileia, capital principal, Nível I, eficiência, inadimplência e cobertura quando publicados e corretamente associados.
9. **Ferramentas Valdemar:** comparação, raio-X de carteira, watchlist e projeções, armazenadas localmente no navegador.

## Regras de segurança

- Nenhum dado ausente é convertido automaticamente em zero.
- Alertas Vermelhos bloqueiam valuation.
- DCF não é aplicado a bancos, seguradoras ou financeiras.
- Bazin de instituições financeiras pode ser exibido com ressalva; a análise de bancos usa IFData quando disponível.
- Seguradoras permanecem com ressalva regulatória quando não houver métrica individual conciliada da SUSEP.
- Units permanecem pendentes quando a composição por classe não puder ser calculada com segurança.
- Histórico de dividendos incompleto aparece como pendente, não como `0/5`.
- Fechamento B3 não é apresentado como cotação em tempo real.
- Nenhum conteúdo constitui recomendação de compra ou venda.

## Fontes

- **B3 COTAHIST:** fechamento, volume e quantidade de negócios.
- **B3 Companhias Listadas:** cadastro, classes, segmentos e proventos.
- **CVM DFP/ITR:** demonstrações financeiras, fluxo de caixa e lucro por ação.
- **Banco Central do Brasil — SGS 432:** Meta Selic definida pelo Copom.
- **Banco Central do Brasil — IFData:** dados trimestrais COSIF/SCR e métricas prudenciais das instituições financeiras.
- **SUSEP:** referência regulatória para seguradoras; métricas individuais permanecem pendentes quando não estiverem disponíveis em fonte aberta conciliável.

## Automação

O workflow `.github/workflows/update.yml` roda automaticamente após o fechamento dos pregões e também pode ser acionado em **Actions → Atualizar dados e publicar → Run workflow**.

O workflow testa regras, atualiza mercado e fundamentos, importa LPA, reconcilia proventos, calcula Bazin/DCF, integra Selic e IFData, atualiza diagnósticos e publica o GitHub Pages.
