from __future__ import annotations

import sqlite3

import app


def main() -> int:
    conn = app.connect()
    try:
        conn.execute(
            """
            UPDATE universe
               SET cnpj = REPLACE(REPLACE(REPLACE(TRIM(COALESCE(cnpj,'')), '.', ''), '/', ''), '-', '')
             WHERE cnpj IS NOT NULL
            """
        )
        conn.commit()
        payload = app.build_site(conn)
        print(
            {
                "status": "OK",
                "empresas": payload.get("count", 0),
                "elegiveis_iniciais": payload.get("initialEligible", 0),
                "aprovadas_parciais": payload.get("qualityPartialApproved", 0),
                "alertas_vermelhos": payload.get("redAlerts", 0),
                "pendentes": payload.get("pendingFundamentals", 0),
                "referencia_fundamentos": payload.get("latestFundamentalsDate", ""),
            }
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
