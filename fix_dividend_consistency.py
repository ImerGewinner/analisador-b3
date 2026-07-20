from __future__ import annotations

import json
from datetime import datetime, timezone

import advanced_analysis as advanced
import app


def complete_history(message: object, status: object) -> bool:
    normalized = advanced.norm_text(message)
    markers = (
        "GETLISTEDCASHDIVIDENDS-5Y",
        "GETLISTEDCASHDIVIDENDS 5Y",
    )
    return str(status or "") == "OK" and any(
        marker in normalized for marker in markers
    )


def run() -> int:
    conn = app.connect()
    try:
        rows = conn.execute(
            """
            SELECT u.ticker,u.cnpj,u.quote_date,di.status AS import_status,
                   di.message AS import_message,
                   f.quality_status,f.roe_avg_5y,f.profit_cagr_5y,
                   f.margin_trend,f.payout,
                   dm.bazin_margin,
                   am.dcf_margin
              FROM universe u
              LEFT JOIN dividend_imports di ON di.root=u.root
              LEFT JOIN fundamentals f ON f.cnpj=u.cnpj
              LEFT JOIN dividend_metrics dm ON dm.ticker=u.ticker
              LEFT JOIN advanced_metrics am ON am.ticker=u.ticker
             WHERE u.principal='SIM'
            """
        ).fetchall()

        updated = 0
        complete_count = 0
        consistent_count = 0
        now = datetime.now(timezone.utc).isoformat()

        for row in rows:
            ticker = str(row["ticker"])
            if complete_history(row["import_message"], row["import_status"]):
                years, streak, cagr = advanced.dividend_history(
                    conn, ticker, str(row["quote_date"] or "")
                )
                complete_count += 1
                if years >= 5:
                    consistent_count += 1
            else:
                years = streak = cagr = None

            classification, reason = advanced.classification_for(
                row,
                {
                    "dcf_margin": row["dcf_margin"],
                    "dividend_years_5y": years or 0,
                },
            )
            conn.execute(
                """
                UPDATE advanced_metrics
                   SET dividend_years_5y=?,
                       dividend_streak_5y=?,
                       dpa_cagr_5y=?,
                       classification=?,
                       classification_reason=?,
                       updated_at=?
                 WHERE ticker=?
                """,
                (years, streak, cagr, classification, reason, now, ticker),
            )
            updated += 1
        conn.commit()

        data_path = app.DOCS_DIR / "data.json"
        payload = json.loads(data_path.read_text(encoding="utf-8"))
        metrics = {
            row["ticker"]: row
            for row in conn.execute(
                """
                SELECT ticker,dividend_years_5y,dividend_streak_5y,dpa_cagr_5y,
                       classification,classification_reason
                  FROM advanced_metrics
                """
            )
        }
        for item in payload.get("items", []):
            metric = metrics.get(item.get("ticker"))
            if not metric:
                continue
            cagr = metric["dpa_cagr_5y"]
            item.update(
                {
                    "anosDividendos5A": metric["dividend_years_5y"],
                    "sequenciaDividendos5A": metric["dividend_streak_5y"],
                    "cagrDpa5A": advanced.fmt_pct(cagr),
                    "cagrDpa5ARaw": cagr,
                    "classificacao3P": metric["classification"],
                    "justificativaClassificacao": metric["classification_reason"],
                }
            )
        payload["dividendHistoryComplete"] = complete_count
        payload["dividendConsistent5y"] = consistent_count
        payload["dividendConsistencyUpdatedAt"] = now
        data_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        status_path = app.DOCS_DIR / "status.json"
        status_payload = (
            json.loads(status_path.read_text(encoding="utf-8"))
            if status_path.exists()
            else {}
        )
        status_payload.update(
            {
                "dividendHistoryComplete": complete_count,
                "dividendConsistent5y": consistent_count,
                "dividendConsistencyUpdatedAt": now,
            }
        )
        status_path.write_text(
            json.dumps(status_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        app.log(
            conn,
            "OK",
            f"Consistência: {complete_count} históricos completos; {consistent_count} com dividendos em 5/5 anos.",
        )
        print(
            json.dumps(
                {
                    "status": "OK",
                    "updated": updated,
                    "complete": complete_count,
                    "consistent_5y": consistent_count,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        app.log(conn, "ERRO", f"Consistência de dividendos: {exc!r}")
        print(f"ERRO consistência de dividendos: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
