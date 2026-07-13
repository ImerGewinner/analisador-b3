from __future__ import annotations

import base64
import json
import re
from datetime import date, datetime, timedelta, timezone

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

import app
import dividends

B3_BASE = (
    "https://sistemaswebb3-listados.b3.com.br/"
    "listedCompaniesProxy/CompanyCall"
)
INITIAL_API = f"{B3_BASE}/GetInitialCompanies/"
SUPPLEMENT_API = f"{B3_BASE}/GetListedSupplementCompany/"
CASH_API = f"{B3_BASE}/GetListedCashDividends/"
PAGE_SIZE = 100
_SESSION = dividends.http_session()
_ROOT_META: dict[str, dict] = {}
_ORIGINAL_CALCULATE_METRICS = dividends.calculate_metrics
_ORIGINAL_IS_DPA_EVENT = dividends.is_dpa_event


def encoded_url(base: str, payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    token = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return base + token


def get_json_compatible(url: str) -> object:
    try:
        response = _SESSION.get(url, timeout=90)
    except requests.exceptions.SSLError:
        urllib3.disable_warnings(InsecureRequestWarning)
        response = _SESSION.get(url, timeout=90, verify=False)

    if response.status_code in {403, 429}:
        # Reaquece a sessão para cookies/limites de proteção do portal B3.
        try:
            _SESSION.get(B3_BASE, timeout=20)
        except Exception:
            pass
        response = _SESSION.get(url, timeout=90, verify=False)

    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        preview = response.text[:250].replace("\n", " ")
        raise RuntimeError(f"B3 retornou conteúdo não JSON: {preview}") from exc


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


def principal_ticker(root: str) -> str:
    meta = _ROOT_META.get(root, {})
    return str(meta.get("principal") or root)


def resolve_company(root: str) -> dict:
    payload = {
        "language": "pt-br",
        "pageNumber": 1,
        "pageSize": 20,
        "company": principal_ticker(root),
    }
    body = get_json_compatible(encoded_url(INITIAL_API, payload))
    if not isinstance(body, dict):
        raise RuntimeError(f"{root}: resposta inválida em GetInitialCompanies")

    results = body.get("results") or []
    if not isinstance(results, list) or not results:
        raise RuntimeError(f"{root}: companhia não localizada na B3")

    exact = next(
        (
            item
            for item in results
            if isinstance(item, dict)
            and str(item.get("issuingCompany") or "").upper() == root.upper()
        ),
        None,
    )
    company = exact or next((item for item in results if isinstance(item, dict)), None)
    if not company:
        raise RuntimeError(f"{root}: cadastro B3 sem objeto de companhia")
    return company


def fetch_supplement(root: str) -> dict | None:
    payload = {"issuingCompany": root.upper(), "language": "pt-br"}
    body = get_json_compatible(encoded_url(SUPPLEMENT_API, payload))

    if isinstance(body, list):
        data = next((item for item in body if isinstance(item, dict)), None)
    elif isinstance(body, dict):
        data = body
    else:
        data = None

    if not data:
        return None

    events = data.get("cashDividends") or data.get("CashDividends") or []
    if not isinstance(events, list):
        events = []

    # Somente aceitamos a fonte suplementar quando ela trouxe eventos.
    # Lista vazia segue para a consulta histórica, evitando DPA zero falso.
    if events:
        info = data.get("info") or data.get("Info") or {}
        if not isinstance(info, dict):
            info = {}
        return {
            "info": info,
            "cashDividends": events,
            "sourceMode": "GetListedSupplementCompany",
            "dividendDataStatus": "VALIDADO",
        }
    return None


def historical_page(trading_name: str, page_number: int) -> dict:
    payload = {
        "language": "pt-br",
        "pageNumber": page_number,
        "pageSize": PAGE_SIZE,
        "tradingName": trading_name,
    }
    body = get_json_compatible(encoded_url(CASH_API, payload))
    if not isinstance(body, dict):
        raise RuntimeError("Resposta inválida em GetListedCashDividends")
    return body


def historical_to_event(root: str, row: dict) -> dict | None:
    value = dividends.parse_decimal(row.get("valueCash"))
    if value is None or value < 0:
        return None

    return {
        "assetIssued": resolve_ticker(root, row.get("typeStock")),
        "paymentDate": "",
        "rate": value,
        "relatedTo": "",
        "approvedOn": row.get("dateApproval") or "",
        "label": row.get("corporateAction") or "Provento em dinheiro",
        "lastDatePrior": row.get("lastDatePriorEx") or "",
        "remarks": "Fonte B3 GetListedCashDividends",
    }


def fetch_historical(root: str) -> dict:
    company = resolve_company(root)
    trading_name = str(company.get("tradingName") or "").replace("/", "").replace(".", "").strip()
    if not trading_name:
        raise RuntimeError(f"{root}: tradingName ausente no cadastro B3")

    first = historical_page(trading_name, 1)
    page_meta = first.get("page") or {}
    total_pages = int(page_meta.get("totalPages") or 1)
    total_records = int(page_meta.get("totalRecords") or 0)

    raw_rows: list[dict] = []
    first_results = first.get("results") or []
    if isinstance(first_results, list):
        raw_rows.extend(item for item in first_results if isinstance(item, dict))

    # Para DPA 12M, não é necessário baixar décadas de histórico.
    # Baixamos páginas até ultrapassar 400 dias ou até o fim da paginação.
    cutoff = date.today() - timedelta(days=400)
    max_pages = min(total_pages, 8)
    for page_number in range(2, max_pages + 1):
        if raw_rows:
            dates = [
                dividends.iso_date(item.get("lastDatePriorEx"))
                for item in raw_rows[-PAGE_SIZE:]
            ]
            valid_dates = [d for d in dates if d]
            if valid_dates:
                try:
                    oldest = min(date.fromisoformat(d) for d in valid_dates)
                    if oldest < cutoff:
                        break
                except ValueError:
                    pass

        page = historical_page(trading_name, page_number)
        results = page.get("results") or []
        if not isinstance(results, list) or not results:
            break
        raw_rows.extend(item for item in results if isinstance(item, dict))

    events: list[dict] = []
    seen: set[tuple] = set()
    for row in raw_rows:
        event = historical_to_event(root, row)
        if not event:
            continue
        key = (
            event["assetIssued"],
            event["label"],
            event["approvedOn"],
            event["lastDatePrior"],
            round(float(event["rate"]), 12),
        )
        if key in seen:
            continue
        seen.add(key)
        events.append(event)

    # Ausência só é considerada confirmada quando o cadastro foi resolvido e a
    # API histórica respondeu validamente com zero registros.
    status = "VALIDADO" if events else "AUSENCIA_CONFIRMADA"
    return {
        "info": {},
        "cashDividends": events,
        "sourceMode": "GetListedCashDividends",
        "tradingNameUsed": trading_name,
        "totalRecords": total_records,
        "dividendDataStatus": status,
    }


def fetch_supplement_compatible(root: str) -> dict:
    supplement_error: Exception | None = None
    try:
        supplement = fetch_supplement(root)
        if supplement:
            return supplement
    except Exception as exc:
        supplement_error = exc

    try:
        return fetch_historical(root)
    except Exception as historical_error:
        raise RuntimeError(
            f"{root}: proventos não conciliados; "
            f"suplementar={supplement_error!r}; histórico={historical_error!r}"
        ) from historical_error


def is_dpa_event_compatible(label: object) -> bool:
    text = dividends.norm_text(label)
    return (
        _ORIGINAL_IS_DPA_EVENT(label)
        or "JRS CAP PROPRIO" in text
        or text.startswith("JRS")
    )


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
