from __future__ import annotations

import json

import run_dividends


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
    print(json.dumps({"lpa_cvm_aplicado": updated, "units_mantidas_pendentes": True}, ensure_ascii=False))


if __name__ == "__main__":
    run_dividends.hydrate_share_counts_from_eps = hydrate_share_counts_from_eps_safe
    raise SystemExit(run_dividends.main())
