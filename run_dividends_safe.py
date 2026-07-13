from __future__ import annotations

import json

import dividends
import run_dividends


_BASE_CALCULATE_METRICS = run_dividends._ORIGINAL_CALCULATE_METRICS


def hydrate_share_counts_from_eps_safe(conn) -> None:
    rows = conn.execute(
        """
        SELECT u.root,u.ticker,f.profit_ltm,e.eps_on_ltm,e.eps_pn_ltm
          FROM universe u
          LEFT JOIN fundamentals f ON f.cnpj=u.cnpj
          LEFT JOIN eps_metrics e ON e.cnpj=u.cnpj
         WHERE u.principal='SIM'
        """
    )
    updated = 0
    for row in rows:
        share_type = run_dividends.share_type_for_ticker(row["ticker"])
        if share_type == "ON":
            eps = row["eps_on_ltm"]
        elif share_type.startswith("PN"):
            eps = row["eps_pn_ltm"]
        else:
            # Units exigem composição por classe; não estimar para não distorcer payout.
            continue
        profit = row["profit_ltm"]
        if eps is None or profit is None or float(eps) <= 0 or float(profit) <= 0:
            continue
        estimated_shares = round(float(profit) / float(eps))
        if estimated_shares <= 0:
            continue
        conn.execute(
            """
            UPDATE dividend_imports
               SET total_shares=COALESCE(NULLIF(total_shares,0),?)
             WHERE root=?
            """,
            (estimated_shares, row["root"]),
        )
        updated += 1
    conn.commit()
    print(
        json.dumps(
            {"lpa_cvm_aplicado": updated, "units_mantidas_pendentes": True},
            ensure_ascii=False,
        )
    )


def bazin_label(margin: float) -> str:
    if margin >= 0.10:
        return "ATRATIVA PELO MÉTODO"
    if margin >= 0:
        return "NEUTRA PELO MÉTODO"
    return "CARA PELO MÉTODO"


def calculate_metrics_validated(conn):
    counters = _BASE_CALCULATE_METRICS(conn)

    # DPA zero confirmado não representa margem de -100%.
    conn.execute(
        """
        UPDATE dividend_metrics
           SET bazin_ceiling=NULL,
               bazin_margin=NULL,
               bazin_status='SEM PROVENTOS 12M — NÃO APLICÁVEL',
               valuation_status='NÃO APLICÁVEL — sem dividendos/JCP validados nos 12M'
         WHERE dpa_12m=0
        """
    )

    # Para bancos, seguradoras e financeiras, o Bazin pode ser calculado porque
    # depende de DPA e cotação, não de EBITDA. O resultado permanece com ressalva
    # até Basileia, inadimplência, cobertura e eficiência serem integrados.
    financial_rows = conn.execute(
        """
        SELECT dm.ticker,dm.dpa_12m,u.price
          FROM dividend_metrics dm
          JOIN universe u ON u.ticker=dm.ticker
          JOIN fundamentals f ON f.cnpj=dm.cnpj
         WHERE f.quality_status='APROVADA PARCIAL — SETOR FINANCEIRO'
           AND dm.dpa_12m>0
           AND u.price>0
        """
    ).fetchall()
    financial_calculated = 0
    for row in financial_rows:
        ceiling = float(row["dpa_12m"]) / dividends.BAZIN_YIELD
        margin = ceiling / float(row["price"]) - 1
        conn.execute(
            """
            UPDATE dividend_metrics
               SET bazin_ceiling=?,
                   bazin_margin=?,
                   bazin_status=?,
                   valuation_status='CALCULADO COM RESSALVA — setor financeiro; métricas regulatórias pendentes'
             WHERE ticker=?
            """,
            (ceiling, margin, bazin_label(margin), row["ticker"]),
        )
        financial_calculated += 1

    conn.commit()

    row = conn.execute(
        """
        SELECT
          SUM(CASE WHEN
              (valuation_status LIKE 'LIBERADO%' OR valuation_status LIKE 'CALCULADO COM RESSALVA%')
              AND dpa_12m>0 THEN 1 ELSE 0 END) AS enabled,
          SUM(CASE WHEN bazin_status='ATRATIVA PELO MÉTODO' AND dpa_12m>0 THEN 1 ELSE 0 END) AS attractive,
          SUM(CASE WHEN dpa_12m IS NOT NULL THEN 1 ELSE 0 END) AS with_dpa,
          COUNT(*) AS processed
        FROM dividend_metrics
        """
    ).fetchone()
    counters.update(
        {
            "processed": int(row["processed"] or 0),
            "with_dpa": int(row["with_dpa"] or 0),
            "bazin_enabled": int(row["enabled"] or 0),
            "bazin_attractive": int(row["attractive"] or 0),
        }
    )
    print(
        json.dumps(
            {
                "validacao_bazin": "OK",
                "bazin_calculado_com_dpa_positivo": counters["bazin_enabled"],
                "financeiras_com_ressalva": financial_calculated,
            },
            ensure_ascii=False,
        )
    )
    return counters


if __name__ == "__main__":
    # Invalida o cache contaminado pelas consultas antigas que marcavam uma
    # resposta vazia como sucesso.
    dividends.imported_recently = lambda conn, root: False
    run_dividends.hydrate_share_counts_from_eps = hydrate_share_counts_from_eps_safe
    run_dividends._ORIGINAL_CALCULATE_METRICS = calculate_metrics_validated
    raise SystemExit(run_dividends.main())
