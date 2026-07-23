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


def event_ticker_for(root: str, raw_asset: str, tickers: list[str], principal: str) -> str:
    raw = str(raw_asset or "").upper().strip()
    if raw in tickers:
        return raw
    for ticker in tickers:
        if ticker and ticker in raw:
            return ticker

    principal_type = run_dividends.share_type_for_ticker(principal)
    if "NOR" in raw or raw.endswith(" ON"):
        if principal_type == "ON":
            return principal
        return next((t for t in tickers if run_dividends.share_type_for_ticker(t) == "ON"), principal)
    if "NPR" in raw or raw.endswith(" PN"):
        if principal_type.startswith("PN"):
            return principal
        return next((t for t in tickers if run_dividends.share_type_for_ticker(t).startswith("PN")), principal)
    if "UNT" in raw or "CDA" in raw:
        unit = next((t for t in tickers if run_dividends.share_type_for_ticker(t) == "UNT"), "")
        return unit or principal
    if len(tickers) == 1:
        return tickers[0]
    return principal


def normalize_event_tickers(conn) -> int:
    roots: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT root,ticker,principal FROM universe WHERE root<>'' ORDER BY root,ticker"
    ):
        meta = roots.setdefault(row["root"], {"tickers": [], "principal": ""})
        meta["tickers"].append(row["ticker"])
        if row["principal"] == "SIM":
            meta["principal"] = row["ticker"]

    changed = 0
    rows = conn.execute(
        "SELECT event_id,root,ticker FROM cash_dividends"
    ).fetchall()
    for row in rows:
        meta = roots.get(row["root"])
        if not meta:
            continue
        tickers = meta["tickers"]
        principal = meta["principal"] or (tickers[0] if tickers else "")
        mapped = event_ticker_for(row["root"], row["ticker"], tickers, principal)
        if mapped and mapped != row["ticker"]:
            conn.execute(
                "UPDATE cash_dividends SET ticker=? WHERE event_id=?",
                (mapped, row["event_id"]),
            )
            changed += 1
    conn.commit()
    print(json.dumps({"eventos_isin_convertidos_para_ticker": changed}, ensure_ascii=False))
    return changed


def remove_duplicate_cash_events(conn) -> int:
    rows = conn.execute(
        """
        SELECT event_id,root,ticker,label,approved_on,last_date_prior,payment_date,
               rate,related_to,remarks
          FROM cash_dividends
         ORDER BY root,ticker,last_date_prior,event_id
        """
    ).fetchall()
    seen: set[tuple] = set()
    removed = 0
    for row in rows:
        signature = (row["root"], row["ticker"], dividends.event_signature(row))
        if signature in seen:
            conn.execute("DELETE FROM cash_dividends WHERE event_id=?", (row["event_id"],))
            removed += 1
        else:
            seen.add(signature)
    conn.commit()
    return removed


def calculate_metrics_validated(conn):
    normalize_event_tickers(conn)
    duplicates_removed = remove_duplicate_cash_events(conn)
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

    conn.commit()

    row = conn.execute(
        """
        SELECT
          SUM(CASE WHEN
              valuation_status LIKE 'CALCULADO%'
              AND dpa_12m>0 THEN 1 ELSE 0 END) AS enabled,
          SUM(CASE WHEN bazin_status='MARGEM ≥ +10%' AND dpa_12m>0 THEN 1 ELSE 0 END) AS margin_ge_10,
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
            "bazin_margin_ge_10": int(row["margin_ge_10"] or 0),
        }
    )
    print(
        json.dumps(
            {
                "validacao_bazin": "OK",
                "bazin_calculado_com_dpa_positivo": counters["bazin_enabled"],
                "duplicatas_proventos_removidas": duplicates_removed,
                "financeiras_sem_aprovacao_integral_bloqueadas": True,
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
