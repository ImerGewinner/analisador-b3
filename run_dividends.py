from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

import app
import dividends

B3_CASH_API = (
    "https://sistemaswebb3-listados.b3.com.br/"
    "listedCompaniesProxy/CompanyCall/GetListedCashDividends/"
)
_SESSION = dividends.http_session()
_ROOT_META: dict[str, dict] = {}
_ORIGINAL_CALCULATE_METRICS = dividends.calculate_metrics
_ORIGINAL_IS_DPA_EVENT = dividends.is_dpa_event


def encoded_url(base: str, payload: dict) -> str:
    token = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return base + token


def get_json_compatible(url: str) -> dict:
    try:
        response = _SESSION.get(url, timeout=90)
    except requests.exceptions.SSLError:
        urllib3.disable_warnings(InsecureRequestWarning)
        response = _SESSION.get(url, timeout=90, verify=False)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        preview = response.text[:180].replace("\n", " ")
        raise RuntimeError(f"B3 retornou conteúdo não JSON: {preview}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Resposta B3 inválida: {type(data).__name__}")
    return data


def share_type_for_ticker(ticker: str) -> str:
    match = re.search(r"(11|[3-8])$", ticker)
    suffix = match.group(1) if match else ""
    return {
        "3": "ON",
        "4": "PN",
        "5": "PNA",
        "6": "PNB",
        "7": "PNC",
        "8": "PND",
        "11": "UNT",
    }.get(suffix, "")


def resolve_ticker(root: str, type_stock: object) -> str:
    meta = _ROOT_META.get(root, {})
    tickers = list(meta.get("tickers") or [])
    principal = str(meta.get("principal") or "")
    wanted = dividends.norm_text(type_stock).split(" ")[0]
    for ticker in tickers:
        if share_type_for_ticker(ticker) == wanted:
            return ticker
    if wanted == "PN":
        for ticker in tickers:
            if share_type_for_ticker(ticker).startswith("PN"):
                return ticker
    return principal or (tickers[0] if tickers else f"{root}3")


def cash_fallback(root: str, primary_error: Exception) -> dict:
    meta = _ROOT_META.get(root, {})
    trading_name = re.sub(r"[^A-Z0-9 ]+", "", dividends.norm_text(meta.get("trading_name")))
    if not trading_name:
        raise RuntimeError(f"{root}: sem nome de negociação para fonte alternativa") from primary_error

    url = encoded_url(
        B3_CASH_API,
        {
            "tradingName": trading_name,
            "language": "pt-br",
            "pageNumber": 1,
            "pageSize": 9999,
        },
    )
    body = get_json_compatible(url)
    results = body.get("results") or []
    if not isinstance(results, list):
        raise RuntimeError(f"{root}: resposta sem lista de proventos") from primary_error

    events: list[dict] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        value = dividends.parse_decimal(row.get("valueCash"))
        ratio = dividends.parse_decimal(row.get("ratio")) or 1.0
        quoted = dividends.parse_decimal(row.get("quotedPerShares")) or 1.0
        if value is None or quoted == 0:
            continue
        rate = value * ratio / quoted
        events.append(
            {
                "assetIssued": resolve_ticker(root, row.get("typeStock")),
                "paymentDate": "",
                "rate": rate,
                "relatedTo": "",
                "approvedOn": row.get("dateApproval") or "",
                "label": row.get("corporateAction") or "Provento em dinheiro",
                "lastDatePrior": row.get("lastDatePriorEx") or "",
                "remarks": "Fonte alternativa B3 GetListedCashDividends",
            }
        )

    return {
        "info": {},
        "cashDividends": events,
        "fallback": "GetListedCashDividends",
        "primaryError": repr(primary_error),
    }


def fetch_supplement_compatible(root: str) -> dict:
    try:
        return get_json_compatible(dividends.payload_url(root))
    except Exception as exc:
        return cash_fallback(root, exc)


def is_dpa_event_compatible(label: object) -> bool:
    text = dividends.norm_text(label)
    return _ORIGINAL_IS_DPA_EVENT(label) or "JRS CAP PROPRIO" in text or text.startswith("JRS")


def hydrate_share_counts_from_eps(conn) -> None:
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
        share_type = share_type_for_ticker(row["ticker"])
        eps = row["eps_on_ltm"] if share_type == "ON" else row["eps_pn_ltm"]
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
    print(json.dumps({"lpa_cvm_aplicado": updated}, ensure_ascii=False))


def calculate_metrics_with_eps(conn):
    hydrate_share_counts_from_eps(conn)
    return _ORIGINAL_CALCULATE_METRICS(conn)


def load_root_metadata() -> None:
    conn = app.connect()
    try:
        rows = conn.execute(
            """
            SELECT root,ticker,trading_name,principal
              FROM universe
             WHERE root<>''
             ORDER BY root,ticker
            """
        )
        for row in rows:
            meta = _ROOT_META.setdefault(
                row["root"],
                {"tickers": [], "trading_name": "", "principal": ""},
            )
            meta["tickers"].append(row["ticker"])
            if row["trading_name"]:
                meta["trading_name"] = row["trading_name"]
            if row["principal"] == "SIM":
                meta["principal"] = row["ticker"]
    finally:
        conn.close()


def main() -> int:
    load_root_metadata()
    dividends.fetch_supplement = fetch_supplement_compatible
    dividends.is_dpa_event = is_dpa_event_compatible
    dividends.calculate_metrics = calculate_metrics_with_eps
    started = datetime.now(timezone.utc).isoformat()
    print(json.dumps({"etapa": "proventos_b3", "inicio": started}, ensure_ascii=False))
    return dividends.run()


if __name__ == "__main__":
    raise SystemExit(main())
