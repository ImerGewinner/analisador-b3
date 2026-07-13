from __future__ import annotations

import json
from datetime import datetime, timezone

import app

SAMPLES = ["PETR", "ITSA", "ABEV", "BBSE", "VALE", "CXSE"]


def main() -> int:
    conn = app.connect()
    try:
        report: dict = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "summary": {},
            "roots": {},
        }
        summary = conn.execute(
            """
            SELECT
              COUNT(*) AS imports,
              SUM(CASE WHEN status='OK' THEN 1 ELSE 0 END) AS ok,
              SUM(CASE WHEN status='ERRO' THEN 1 ELSE 0 END) AS errors,
              (SELECT COUNT(*) FROM cash_dividends) AS events,
              (SELECT COUNT(*) FROM cash_dividends WHERE eligible_dpa=1) AS eligible_events
            FROM dividend_imports
            """
        ).fetchone()
        report["summary"] = dict(summary) if summary else {}

        for root in SAMPLES:
            imp = conn.execute(
                "SELECT * FROM dividend_imports WHERE root=?", (root,)
            ).fetchone()
            rows = conn.execute(
                """
                SELECT root,ticker,label,approved_on,last_date_prior,payment_date,rate,
                       eligible_dpa,remarks
                  FROM cash_dividends
                 WHERE root=?
                 ORDER BY last_date_prior DESC, ticker, label
                 LIMIT 40
                """,
                (root,),
            ).fetchall()
            report["roots"][root] = {
                "import": dict(imp) if imp else None,
                "eventCount": conn.execute(
                    "SELECT COUNT(*) FROM cash_dividends WHERE root=?", (root,)
                ).fetchone()[0],
                "eligibleEventCount": conn.execute(
                    "SELECT COUNT(*) FROM cash_dividends WHERE root=? AND eligible_dpa=1", (root,)
                ).fetchone()[0],
                "events": [dict(row) for row in rows],
            }

        (app.DOCS_DIR / "dividends_diagnostics.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report["summary"], ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
