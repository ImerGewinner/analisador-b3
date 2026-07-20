from __future__ import annotations

import json
from datetime import datetime, timezone

import advanced_analysis as advanced
import app


def run() -> int:
    conn = app.connect()
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
            count = conn.execute(
                """
                SELECT COUNT(*) AS total
                  FROM fcf_history
                 WHERE cnpj=? AND fcf IS NOT NULL
                """,
                (row["cnpj"],),
            ).fetchone()["total"]
            if str(row["dcf_status"] or "").startswith("CALCULADO") and int(count or 0) < 3:
                conn.execute(
                    """
                    UPDATE advanced_metrics
                       SET fcf_avg_3y=NULL,
                           dcf_growth=NULL,
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
                SELECT ticker,fcf_avg_3y,dcf_growth,dcf_fair_price,dcf_margin,dcf_status
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
        attractive = conn.execute(
            "SELECT COUNT(*) FROM advanced_metrics WHERE dcf_status LIKE 'CALCULADO%' AND dcf_margin>=0.10"
        ).fetchone()[0]
        now = datetime.now(timezone.utc).isoformat()
        payload["dcfEnabled"] = int(enabled)
        payload["dcfAttractive"] = int(attractive)
        payload["dcfGovernanceUpdatedAt"] = now
        payload.setdefault("methodology", {})["dcf"] = (
            "DCF simplificado somente para não financeiras aprovadas e com três exercícios "
            "completos de FCF: média 3A; g limitado a 6%; WACC 12%; desconto de segurança "
            "de 25%; valor dividido pela quantidade de ações."
        )
        data_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        status_path = app.DOCS_DIR / "status.json"
        status_payload = json.loads(status_path.read_text(encoding="utf-8"))
        status_payload.update(
            {
                "dcfEnabled": int(enabled),
                "dcfAttractive": int(attractive),
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
                    "dcf_attractive": int(attractive),
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
