from __future__ import annotations

from datetime import date, timedelta

import dividends
import run_dividends
import run_dividends_full as full


def historical_all_candidates(root: str) -> dict:
    cutoff = date.today() - timedelta(days=6 * 365 + 60)
    valid_empty: list[str] = []
    errors: list[str] = []
    for trading_name in full.historical_candidates(root):
        try:
            first = run_dividends.historical_page(trading_name, 1)
            page_meta = first.get("page") or {}
            total_pages = int(page_meta.get("totalPages") or 1)
            total_records = int(page_meta.get("totalRecords") or 0)
            raw_rows = [
                item for item in (first.get("results") or []) if isinstance(item, dict)
            ]
            for page_number in range(2, min(total_pages, 30) + 1):
                recent_dates = [
                    dividends.iso_date(item.get("lastDatePriorEx"))
                    for item in raw_rows[-run_dividends.PAGE_SIZE :]
                ]
                valid_dates = [item for item in recent_dates if item]
                if valid_dates:
                    try:
                        if min(date.fromisoformat(item) for item in valid_dates) < cutoff:
                            break
                    except ValueError:
                        pass
                page = run_dividends.historical_page(trading_name, page_number)
                results = page.get("results") or []
                if not isinstance(results, list) or not results:
                    break
                raw_rows.extend(item for item in results if isinstance(item, dict))

            events: list[dict] = []
            seen: set[tuple] = set()
            for raw in raw_rows:
                event = run_dividends.historical_to_event(root, raw)
                if not event:
                    continue
                event_date = dividends.iso_date(event.get("lastDatePrior"))
                if event_date:
                    try:
                        if date.fromisoformat(event_date) < cutoff:
                            continue
                    except ValueError:
                        pass
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
            if events:
                return {
                    "info": {},
                    "cashDividends": events,
                    "sourceMode": "GetListedCashDividends-5Y",
                    "tradingNameUsed": trading_name,
                    "totalRecords": total_records,
                    "dividendDataStatus": "VALIDADO",
                }
            valid_empty.append(trading_name)
        except Exception as exc:
            errors.append(f"{trading_name}: {exc!r}")

    if valid_empty:
        return {
            "info": {},
            "cashDividends": [],
            "sourceMode": "GetListedCashDividends-5Y",
            "tradingNameUsed": " | ".join(valid_empty),
            "totalRecords": 0,
            "dividendDataStatus": "AUSENCIA_CONFIRMADA",
        }
    raise RuntimeError(f"{root}: todas as consultas históricas falharam: {errors}")


def fetch_dividends_v2(root: str) -> dict:
    supplement = None
    supplement_error = None
    try:
        supplement = run_dividends.fetch_supplement(root)
    except Exception as exc:
        supplement_error = exc

    historical = None
    historical_error = None
    try:
        historical = historical_all_candidates(root)
    except Exception as exc:
        historical_error = exc

    historical_events = (
        historical.get("cashDividends") if isinstance(historical, dict) else []
    ) or []
    supplement_events = (
        supplement.get("cashDividends") if isinstance(supplement, dict) else []
    ) or []

    if historical_events:
        info = (supplement or {}).get("info") or (supplement or {}).get("Info") or {}
        if isinstance(info, dict) and info:
            historical["info"] = info
        return historical

    if supplement_events:
        result = dict(supplement)
        result["cashDividends"] = supplement_events
        result["dividendDataStatus"] = "VALIDADO"
        result["sourceMode"] = "GetListedSupplementCompany-12M"
        result["historyStatus"] = "PENDENTE_HISTORICO_5Y"
        return result

    if historical is not None:
        return historical

    return {
        "info": {},
        "cashDividends": [],
        "dividendDataStatus": "PENDENTE_FONTE",
        "sourceMode": "indisponível",
        "message": f"suplementar={supplement_error!r}; histórico={historical_error!r}",
    }


if __name__ == "__main__":
    full.fetch_dividends_governed = fetch_dividends_v2
    raise SystemExit(full.run())
