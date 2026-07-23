from __future__ import annotations

import advanced_analysis as advanced
import dividends
import run_advanced_safe as safe


def governed_dividend_history_v2(conn, ticker: str, quote_date: str):
    row = conn.execute(
        """
        SELECT di.status,di.message,dm.dividend_integrity_status
          FROM universe u
          LEFT JOIN dividend_imports di ON di.root=u.root
          LEFT JOIN dividend_metrics dm ON dm.ticker=u.ticker
         WHERE u.ticker=?
        """,
        (ticker,),
    ).fetchone()
    message = advanced.norm_text(row["message"]) if row else ""
    complete = bool(
        row
        and row["status"] == "OK"
        and "GETLISTEDCASHDIVIDENDS 5Y" in message
        and row["dividend_integrity_status"] != dividends.CLASS_MISMATCH_STATUS
    )
    if not complete:
        safe._incomplete_dividend_history.add(ticker)
        return 0, 0, None
    return safe._original_dividend_history(conn, ticker, quote_date)


if __name__ == "__main__":
    safe._incomplete_dividend_history.clear()
    advanced.dividend_history = governed_dividend_history_v2
    advanced.compute_metrics = safe.compute_metrics_governed
    raise SystemExit(advanced.run())
