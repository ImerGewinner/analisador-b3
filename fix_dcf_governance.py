from __future__ import annotations

import json
from datetime import datetime, timezone

import advanced_analysis as advanced
import app


def run() -> int:
    conn = app.connect()
    advanced.ensure_schema(conn)
    try:
        rows = conn.execute(
            """
            SELECT am.ticker,dm.cnpj,am.dcf_status,am.dcf_margin
              FROM advanced_metrics am
              LEFT JOIN dividend_metrics dm ON dm.ticker=am.ticker
            """
        ).fetchall()
        invalidated = 0
        for row in rows:
            years = [
                int(item["year"])
                for item in conn.execute(
                """
                SELECT year
                  FROM fcf_history
                 WHERE cnpj=? AND fcf IS NOT NULL
                 ORDER BY year DESC LIMIT 3
                """,
                (row["cnpj"],),
                )
            ]
            complete = len(years) == 3 and max(years) - min(years) == 2
            if str(row["dcf_status"] or "").startswith("CALCULADO") and not complete:
                conn.execute(
                    """
                    UPDATE advanced_metrics
                       SET fcf_avg_3y=NULL,
                           dcf_growth=NULL,
                           fcf_cagr_3y=NULL,
                           dcf_enterprise_value=NULL,
                           dcf_equity_value=NULL,
                           dcf_intrinsic_price=NULL,
                           dcf_fair_price=NULL,
                           dcf_margin=NULL,
                           dcf_status='PENDENTE — exige três exercícios completos de FCF',
                           updated_at=?
                     WHERE ticker=?
                    """,
                    (datetime.now(timezone.utc).isoformat(), row["ticker"]),
                )
                invalidated += 1
        conn.commit()

        metrics = {
            row["ticker"]: row
            for row in conn.execute(
                """
                SELECT ticker,fcf_avg_3y,fcf_cagr_3y,dcf_growth,
                       dcf_enterprise_value,dcf_equity_value,dcf_intrinsic_price,
                       dcf_fair_price,dcf_margin,dcf_status
                  FROM advanced_metrics
                """
            )
        }
        data_path = app.DOCS_DIR / "data.json"
        payload = json.loads(data_path.read_text(encoding="utf-8"))
        for item in payload.get("items", []):
            metric = metrics.get(item.get("ticker"))
            if not metric:
                continue
            item.update(
                {
                    "fcfMedio3A": advanced.fmt_money(metric["fcf_avg_3y"]),
                    "fcfMedio3ARaw": metric["fcf_avg_3y"],
                    "crescimentoDcf": advanced.fmt_pct(metric["dcf_growth"]),
                    "crescimentoDcfRaw": metric["dcf_growth"],
                    "cagrFcf3A": advanced.fmt_pct(metric["fcf_cagr_3y"]),
                    "cagrFcf3ARaw": metric["fcf_cagr_3y"],
                    "valorFirmaDcf": advanced.fmt_money(metric["dcf_enterprise_value"]),
                    "valorFirmaDcfRaw": metric["dcf_enterprise_value"],
                    "valorPatrimonioDcf": advanced.fmt_money(metric["dcf_equity_value"]),
                    "valorPatrimonioDcfRaw": metric["dcf_equity_value"],
                    "valorIntrinsecoDcf": advanced.fmt_money(metric["dcf_intrinsic_price"]),
                    "valorIntrinsecoDcfRaw": metric["dcf_intrinsic_price"],
                    "precoJustoDcf": advanced.fmt_money(metric["dcf_fair_price"]),
                    "precoJustoDcfRaw": metric["dcf_fair_price"],
                    "margemDcf": advanced.fmt_pct(metric["dcf_margin"], True),
                    "margemDcfRaw": metric["dcf_margin"],
                    "statusDcf": metric["dcf_status"],
                }
            )
        enabled = conn.execute(
            "SELECT COUNT(*) FROM advanced_metrics WHERE dcf_status LIKE 'CALCULADO%'"
        ).fetchone()[0]
        margin_ge_10 = conn.execute(
            "SELECT COUNT(*) FROM advanced_metrics WHERE dcf_status LIKE 'CALCULADO%' AND dcf_margin>=0.10"
        ).fetchone()[0]
        now = datetime.now(timezone.utc).isoformat()
        payload["dcfEnabled"] = int(enabled)
        payload["dcfMarginGe10"] = int(margin_ge_10)
        payload.pop("dcfAttractive", None)
        payload["dcfGovernanceUpdatedAt"] = now
        payload.setdefault("methodology", {})["dcf"] = (
            "DCF somente para não financeiras aprovadas e com três exercícios completos e "
            "consecutivos de FCFF. Dez fluxos e o terminal são descontados; dívida líquida é "
            "subtraída; WACC 12%, crescimento terminal 3% e margem de segurança de 25%."
        )
        data_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        status_path = app.DOCS_DIR / "status.json"
        status_payload = json.loads(status_path.read_text(encoding="utf-8"))
        status_payload.pop("dcfAttractive", None)
        status_payload.update(
            {
                "dcfEnabled": int(enabled),
                "dcfMarginGe10": int(margin_ge_10),
                "dcfInvalidatedForMissingYears": invalidated,
                "dcfGovernanceUpdatedAt": now,
            }
        )
        status_path.write_text(
            json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        app.log(
            conn,
            "OK",
            f"DCF governado: {enabled} calculados; {invalidated} bloqueados por menos de 3 anos de FCF.",
        )
        print(
            json.dumps(
                {
                    "status": "OK",
                    "dcf_enabled": int(enabled),
                    "dcf_margin_ge_10": int(margin_ge_10),
                    "invalidated": invalidated,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        app.log(conn, "ERRO", f"Governança DCF: {exc!r}")
        print(f"ERRO governança DCF: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
