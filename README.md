# Analisador B3

Projeto educacional em Python, SQLite, GitHub Actions e GitHub Pages para acompanhar ações brasileiras com dados públicos da B3 e da CVM.

## Fluxo atual

1. **Filtro inicial de liquidez:** volume médio de 20 pregões, frequência de negociação e sessões sem negócio.
2. **Fundamentos CVM:** importação de DFP 2020–2025 e ITR 2025–2026, ajustada automaticamente conforme o ano corrente.
3. **Checklist Anti-Lixo:** ROE médio 5A, crescimento do lucro, dívida líquida/EBITDA, tendência da margem líquida e consistência do lucro.
4. **Regra de parada:** duas ou mais reprovações, prejuízo recorrente ou deterioração acelerada da dívida geram **Alerta Vermelho**.
5. **Valuation bloqueado:** payout e proventos por ação ainda não foram conciliados.

Bancos e seguradoras não usam dívida líquida/EBITDA. A classificação permanece parcial até incluir capitalização, inadimplência, cobertura, provisões e eficiência.

## Fontes

- B3 COTAHIST: fechamento, volume e quantidade de negócios.
- B3 Companhias Listadas: cadastro e segmento.
- CVM DFP/ITR: demonstrações financeiras anuais e trimestrais.

## Automação

O workflow `.github/workflows/update.yml` roda automaticamente após o fechamento dos pregões e também pode ser acionado manualmente em **Actions → Atualizar dados e publicar → Run workflow**.

Nenhum conteúdo constitui recomendação de compra ou venda.
