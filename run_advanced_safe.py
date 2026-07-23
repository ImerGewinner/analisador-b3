from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import advanced_analysis as advanced
import dividends


_original_compute = advanced.compute_metrics
_incomplete_dividend_history: set[str] = set()


def parse_fcf_leaf_accounts(content: bytes, year: int, active: set[str]) -> list[dict]:
    cfo_selected: dict[str, tuple[int, int, float, str]] = {}
    capex_selected: dict[tuple[str, int, int], dict[str, tuple[float, str]]] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = advanced.dfc_members(archive, year)
        if not members:
            raise RuntimeError(f"DFP {year} sem DFC")
        for member, priority in members:
            with archive.open(member) as raw:
                reader = csv.DictReader(
                    io.TextIOWrapper(raw, encoding="latin-1", newline=""), delimiter=";"
                )
                if not reader.fieldnames:
                    continue
                reader.fieldnames = [
                    str(name or "").lstrip("\ufeff").strip() for name in reader.fieldnames
                ]
                for row in reader:
                    cnpj = re.sub(r"\D", "", str(row.get("CNPJ_CIA") or ""))
                    if cnpj not in active or not advanced.norm_text(row.get("ORDEM_EXERC")).startswith("ULTIMO"):
                        continue
                    try:
                        version = int(float(str(row.get("VERSAO") or "0").replace(",", ".")))
                    except ValueError:
                        version = 0
                    account = str(row.get("CD_CONTA") or "").strip()
                    description = advanced.norm_text(row.get("DS_CONTA"))
                    value = advanced.number(row.get("VL_CONTA"), row.get("ESCALA_MOEDA"))
                    if value is None:
                        continue
                    if account == "6.01" or (
                        "CAIXA LIQUIDO" in description and "OPERACION" in description
                    ):
                        candidate = (priority, version, value, member)
                        current = cfo_selected.get(cnpj)
                        if current is None or candidate[:2] > current[:2]:
                            cfo_selected[cnpj] = candidate
                    is_capex = (
                        account.startswith("6.02")
                        and any(term in description for term in ("IMOBILIZADO", "INTANGIVEL"))
                        and any(term in description for term in ("AQUISICAO", "ADICAO", "COMPRA"))
                    )
                    if is_capex:
                        bucket = capex_selected.setdefault((cnpj, priority, version), {})
                        current = bucket.get(account)
                        candidate = (abs(value), member)
                        if current is None or candidate[0] > current[0]:
                            bucket[account] = candidate

    result: list[dict] = []
    for cnpj, (priority, version, cfo, member) in cfo_selected.items():
        account_values = capex_selected.get((cnpj, priority, version), {})
        leaf_values: list[float] = []
        accounts = list(account_values)
        for account in accounts:
            has_child = any(
                other != account and other.startswith(account + ".") for other in accounts
            )
            if not has_child:
                leaf_values.append(account_values[account][0])
        capex = sum(leaf_values) if leaf_values else None
        result.append(
            {
                "cnpj": cnpj,
                "year": year,
                "cfo": cfo,
                "capex": capex,
                "fcf": cfo - capex if capex is not None else None,
                "source_file": member,
            }
        )
    return result


def governed_dividend_history(conn, ticker: str, quote_date: str):
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
    complete = bool(
        row
        and row["status"] == "OK"
        and "GETLISTEDCASHDIVIDENDS-5Y" in advanced.norm_text(row["message"])
        and row["dividend_integrity_status"] != dividends.CLASS_MISMATCH_STATUS
    )
    if not complete:
        _incomplete_dividend_history.add(ticker)
        return 0, 0, None
    return _original_dividend_history(conn, ticker, quote_date)


def compute_metrics_governed(conn, selic):
    counters = _original_compute(conn, selic)
    for ticker in _incomplete_dividend_history:
        conn.execute(
            """
            UPDATE advanced_metrics
               SET dividend_years_5y=NULL,
                   dividend_streak_5y=NULL,
                   dpa_cagr_5y=NULL
             WHERE ticker=?
            """,
            (ticker,),
        )
    conn.commit()
    return counters


_original_dividend_history = advanced.dividend_history
advanced.parse_fcf_package = parse_fcf_leaf_accounts
advanced.dividend_history = governed_dividend_history
advanced.compute_metrics = compute_metrics_governed


if __name__ == "__main__":
    raise SystemExit(advanced.run())
