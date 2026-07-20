from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import app
import dividends
import run_dividends
import run_dividends_safe as safe


def historical_candidates(root: str) -> list[str]:
    meta = run_dividends._ROOT_META.get(root, {})
    values: list[str] = []
    try:
        company = run_dividends.resolve_company(root)
        values.append(str(company.get("tradingName") or ""))
    except Exception:
        pass
    values.extend([str(meta.get("trading_name") or ""), str(meta.get("principal") or ""), root])
    result: list[str] = []
    for raw in values:
        for candidate in (raw.strip(), raw.replace("/", "").replace(".", "").strip(), dividends.norm_text(raw)):
            if candidate and candidate not in result:
                result.append(candidate)
    return result


def fetch_historical_5y(root: str) -> dict:
    last_error: Exception | None = None
    cutoff = date.today() - timedelta(days=6 * 365 + 60)
    for trading_name in historical_candidates(root):
        try:
            first = run_dividends.historical_page(trading_name, 1)
            page_meta = first.get("page") or {}
            total_pages = int(page_meta.get("totalPages") or 1)
            total_records = int(page_meta.get("totalRecords") or 0)
            raw_rows = [item for item in (first.get("results") or []) if isinstance(item, dict)]
            for page_number in range(2, min(total_pages, 30) + 1):
                recent_dates = [dividends.iso_date(item.get("lastDatePriorEx")) for item in raw_rows[-run_dividends.PAGE_SIZE:]]
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
                key = (event["assetIssued"], event["label"], event["approvedOn"], event["lastDatePrior"], round(float(event["rate"]), 12))
                if key in seen:
                    continue
                seen.add(key)
                events.append(event)
            return {
                "info": {},
                "cashDividends": events,
                "sourceMode": "GetListedCashDividends-5Y",
                "tradingNameUsed": trading_name,
                "totalRecords": total_records,
                "dividendDataStatus": "VALIDADO" if events else "AUSENCIA_CONFIRMADA",
            }
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"{root}: histórico B3 não conciliado: {last_error!r}")


def fetch_dividends_governed(root: str) -> dict:
    supplement: dict | None = None
    supplement_error: Exception | None = None
    try:
        supplement = run_dividends.fetch_supplement(root)
    except Exception as exc:
        supplement_error = exc
    try:
        historical = fetch_historical_5y(root)
        if supplement:
            info = supplement.get("info") or supplement.get("Info") or {}
            if isinstance(info, dict) and info:
                historical["info"] = info
        return historical
    except Exception as historical_error:
        if supplement and (supplement.get("cashDividends") or supplement.get("CashDividends")):
            supplement["dividendDataStatus"] = "VALIDADO"
            supplement["sourceMode"] = "GetListedSupplementCompany-fallback"
            return supplement
        return {
            "info": {},
            "cashDividends": [],
            "dividendDataStatus": "PENDENTE_FONTE",
            "sourceMode": "indisponível",
            "message": f"suplementar={supplement_error!r}; histórico={historical_error!r}",
        }


def import_root_governed(conn, root: str) -> tuple[str, int]:
    now = datetime.now(timezone.utc).isoformat()
    try:
        payload = dividends.fetch_supplement(root)
        data_status = str(payload.get("dividendDataStatus") or "VALIDADO")
        info = payload.get("info") or payload.get("Info") or {}
        events = payload.get("cashDividends") or payload.get("CashDividends") or []
        if not isinstance(info, dict):
            info = {}
        if not isinstance(events, list):
            events = []
        total_shares = dividends.parse_integer(info.get("totalNumberShares"))
        common_shares = dividends.parse_integer(info.get("numberCommonShares"))
        preferred_shares = dividends.parse_integer(info.get("numberPreferredShares"))
        rows: list[tuple] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            ticker = str(event.get("assetIssued") or "").strip().upper()
            label = str(event.get("label") or "Provento em dinheiro").strip()
            approved = dividends.iso_date(event.get("approvedOn"))
            last_com = dividends.iso_date(event.get("lastDatePrior"))
            payment = dividends.iso_date(event.get("paymentDate"))
            rate = dividends.parse_decimal(event.get("rate"))
            related = str(event.get("relatedTo") or "").strip()
            remarks = str(event.get("remarks") or "").strip()
            if rate is None or rate < 0:
                continue
            key = dividends.event_key(root, ticker, label, approved, last_com, payment, rate, related)
            rows.append((key, root, ticker, label, approved, last_com, payment, rate, related, remarks, 1 if dividends.is_dpa_event(label) else 0, dividends.B3_PUBLIC_SOURCE, now))
        conn.execute("DELETE FROM cash_dividends WHERE root=?", (root,))
        conn.executemany(
            """INSERT INTO cash_dividends(event_id,root,ticker,label,approved_on,last_date_prior,payment_date,rate,related_to,remarks,eligible_dpa,source_url,imported_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        status = "OK" if data_status in {"VALIDADO", "AUSENCIA_CONFIRMADA"} else "PENDENTE"
        message = payload.get("message") or f"{len(rows)} evento(s); status={data_status}; fonte={payload.get('sourceMode', '')}"
        conn.execute(
            """INSERT INTO dividend_imports(root,imported_at,status,message,total_shares,common_shares,preferred_shares,source_url)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(root) DO UPDATE SET imported_at=excluded.imported_at,status=excluded.status,message=excluded.message,
               total_shares=COALESCE(excluded.total_shares,dividend_imports.total_shares),
               common_shares=COALESCE(excluded.common_shares,dividend_imports.common_shares),
               preferred_shares=COALESCE(excluded.preferred_shares,dividend_imports.preferred_shares),source_url=excluded.source_url""",
            (root, now, status, message, total_shares, common_shares, preferred_shares, dividends.B3_PUBLIC_SOURCE),
        )
        conn.commit()
        return status, len(rows)
    except Exception as exc:
        conn.execute(
            """INSERT INTO dividend_imports(root,imported_at,status,message,source_url) VALUES(?,?,?,?,?)
               ON CONFLICT(root) DO UPDATE SET imported_at=excluded.imported_at,status=excluded.status,message=excluded.message,source_url=excluded.source_url""",
            (root, now, "ERRO", repr(exc), dividends.B3_PUBLIC_SOURCE),
        )
        conn.commit()
        return "ERRO", 0


def run() -> int:
    run_dividends.load_root_metadata()
    dividends.fetch_supplement = fetch_dividends_governed
    dividends.is_dpa_event = run_dividends.is_dpa_event_compatible
    conn = app.connect()
    dividends.ensure_schema(conn)
    try:
        roots = [row[0] for row in conn.execute("SELECT DISTINCT root FROM universe WHERE principal='SIM' AND root<>'' ORDER BY root")]
        summary = {"ok": 0, "pending": 0, "error": 0, "events": 0}
        failures: list[dict] = []
        for index, root in enumerate(roots, 1):
            status, count = import_root_governed(conn, root)
            summary[{"OK": "ok", "PENDENTE": "pending", "ERRO": "error"}.get(status, "error")] += 1
            summary["events"] += count
            if status != "OK":
                detail = conn.execute("SELECT message FROM dividend_imports WHERE root=?", (root,)).fetchone()
                failures.append({"root": root, "status": status, "message": detail["message"] if detail else ""})
            if index % 25 == 0:
                print(f"Proventos B3: {index}/{len(roots)} emissores")

        safe.hydrate_share_counts_from_eps_safe(conn)
        metrics = safe.calculate_metrics_validated(conn)
        run_status = "OK" if not failures else "PARCIAL"
        app.log(conn, run_status, f"Proventos: {summary['ok']} OK, {summary['pending']} pendentes, {summary['error']} erros; Bazin {metrics['bazin_enabled']}.")
        payload = dividends.enrich_site(conn, metrics, {"ok": summary["ok"], "cache": 0, "error": summary["error"], "events": summary["events"]})
        payload["dividendImportsPending"] = summary["pending"]
        payload["failedDividendRoots"] = failures
        (app.DOCS_DIR / "data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        status_path = app.DOCS_DIR / "status.json"
        status_payload = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
        status_payload.update({"status": run_status, "dividendImportsPending": summary["pending"], "dividendImportsError": summary["error"], "failedDividendRoots": failures})
        status_path.write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": run_status, "emissores": len(roots), **summary, **metrics, "falhas": failures}, ensure_ascii=False))
        return 0
    except Exception as exc:
        app.log(conn, "ERRO", f"Proventos/Bazin: {exc!r}")
        print(f"ERRO Proventos/Bazin: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
