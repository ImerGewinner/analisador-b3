from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta, timezone
from statistics import median

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import app
import quality_rules

B3_SUPPLEMENT_API = (
    "https://sistemaswebb3-listados.b3.com.br/"
    "listedCompaniesProxy/CompanyCall/GetListedSupplementCompany/"
)
B3_PUBLIC_SOURCE = "https://sistemaswebb3-listados.b3.com.br/"
BAZIN_YIELD = 0.0775
PAYOUT_MAX = 0.90
REFRESH_HOURS = 18
DPA_OUTLIER_MULTIPLE = 3.0
CLASS_MISMATCH_STATUS = (
    "PENDENTE — eventos localizados apenas em outra classe/Unit do emissor; "
    "conciliar por classe"
)
EXTRAORDINARY_TERMS = (
    "EXTRAORDIN",
    "ESPECIAL",
    "RESERVA DE LUCRO",
    "RESERVAS DE LUCRO",
)


def norm_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().upper()


def parse_decimal(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace("R$", "").replace(" ", "")
    if not raw:
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_integer(value: object) -> int | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else None


def iso_date(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).date().isoformat()
        except ValueError:
            pass
    match = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if match:
        return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
    return text[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text) else ""


def fmt_money(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {text}"


def fmt_pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.2f}%".replace(".", ",")


def http_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 AnalisadorB3Educacional/3.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": B3_PUBLIC_SOURCE,
        }
    )
    return session


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cash_dividends(
          event_id TEXT PRIMARY KEY,
          root TEXT NOT NULL,
          ticker TEXT,
          label TEXT,
          approved_on TEXT,
          last_date_prior TEXT,
          payment_date TEXT,
          rate REAL,
          related_to TEXT,
          remarks TEXT,
          eligible_dpa INTEGER NOT NULL DEFAULT 0,
          source_url TEXT,
          imported_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cash_dividends_ticker_date
          ON cash_dividends(ticker,last_date_prior);
        CREATE INDEX IF NOT EXISTS idx_cash_dividends_root
          ON cash_dividends(root);
        CREATE TABLE IF NOT EXISTS dividend_imports(
          root TEXT PRIMARY KEY,
          imported_at TEXT,
          status TEXT,
          message TEXT,
          total_shares INTEGER,
          common_shares INTEGER,
          preferred_shares INTEGER,
          source_url TEXT
        );
        CREATE TABLE IF NOT EXISTS dividend_metrics(
          ticker TEXT PRIMARY KEY,
          root TEXT,
          cnpj TEXT,
          quote_date TEXT,
          window_start TEXT,
          dpa_12m REAL,
          dividend_yield REAL,
          total_shares INTEGER,
          lpa REAL,
          payout REAL,
          bazin_ceiling REAL,
          bazin_margin REAL,
          bazin_status TEXT,
          valuation_status TEXT,
          events_count INTEGER,
          source_url TEXT,
          updated_at TEXT
        );
        """
    )
    app.ensure_column(conn, "dividend_metrics", "dpa_total_12m", "REAL")
    app.ensure_column(conn, "dividend_metrics", "extraordinary_dpa_12m", "REAL")
    app.ensure_column(conn, "dividend_metrics", "dividend_yield_total", "REAL")
    app.ensure_column(conn, "dividend_metrics", "historical_dpa_median", "REAL")
    app.ensure_column(conn, "dividend_metrics", "dividend_integrity_status", "TEXT")
    conn.commit()


def payload_url(root: str) -> str:
    payload = {"issuingCompany": root, "language": "pt-br"}
    token = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return B3_SUPPLEMENT_API + token


def fetch_supplement(root: str) -> dict:
    response = http_session().get(payload_url(root), timeout=90)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Resposta B3 inválida para {root}")
    return data


def is_dpa_event(label: object) -> bool:
    text = norm_text(label)
    if any(term in text for term in ("RESTITUICAO", "REDUCAO DE CAPITAL", "AMORTIZACAO", "BONIFICACAO")):
        return False
    return any(term in text for term in ("DIVIDENDO", "JUROS", "JCP"))


def is_explicit_extraordinary(event: sqlite3.Row | dict) -> bool:
    text = norm_text(
        " ".join(
            str(event[key] or "")
            for key in ("label", "related_to", "remarks")
            if key in event.keys()
        )
    )
    return any(term in text for term in EXTRAORDINARY_TERMS)


def event_signature(event: sqlite3.Row | dict) -> tuple:
    """Collapse B3 duplicates that differ only beyond useful precision."""

    return (
        norm_text(event["label"]),
        str(event["approved_on"] or ""),
        str(event["last_date_prior"] or ""),
        str(event["payment_date"] or ""),
        round(float(event["rate"] or 0), 8),
        norm_text(event["related_to"]),
    )


def deduplicate_events(events: list[sqlite3.Row] | list[dict]) -> list:
    seen: set[tuple] = set()
    result: list = []
    for event in events:
        signature = event_signature(event)
        if signature in seen:
            continue
        seen.add(signature)
        result.append(event)
    return result


def event_key(root: str, ticker: str, label: str, approved: str, last_com: str, payment: str, rate: float, related: str) -> str:
    raw = "|".join([root, ticker, label, approved, last_com, payment, f"{rate:.12f}", related])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def imported_recently(conn: sqlite3.Connection, root: str) -> bool:
    row = conn.execute("SELECT imported_at,status FROM dividend_imports WHERE root=?", (root,)).fetchone()
    if not row or row["status"] != "OK" or not row["imported_at"]:
        return False
    try:
        imported = datetime.fromisoformat(row["imported_at"].replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - imported < timedelta(hours=REFRESH_HOURS)


def has_sibling_class_events(
    conn: sqlite3.Connection,
    root: str,
    ticker: str,
    start: str,
    end: str,
) -> bool:
    return conn.execute(
        """
        SELECT 1
          FROM cash_dividends
         WHERE root=? AND ticker<>? AND eligible_dpa=1
           AND last_date_prior>=? AND last_date_prior<=?
         LIMIT 1
        """,
        (root, ticker, start, end),
    ).fetchone() is not None


def import_root(conn: sqlite3.Connection, root: str, force: bool = False) -> tuple[str, int]:
    if not force and imported_recently(conn, root):
        count = int(conn.execute("SELECT COUNT(*) FROM cash_dividends WHERE root=?", (root,)).fetchone()[0])
        return "CACHE", count
    now = datetime.now(timezone.utc).isoformat()
    try:
        payload = fetch_supplement(root)
        info = payload.get("info") or payload.get("Info") or {}
        events = payload.get("cashDividends") or payload.get("CashDividends") or []
        if not isinstance(info, dict):
            info = {}
        if not isinstance(events, list):
            events = []
        total_shares = parse_integer(info.get("totalNumberShares"))
        common_shares = parse_integer(info.get("numberCommonShares"))
        preferred_shares = parse_integer(info.get("numberPreferredShares"))
        rows: list[tuple] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            ticker = str(event.get("assetIssued") or "").strip().upper()
            label = str(event.get("label") or "Provento em dinheiro").strip()
            approved = iso_date(event.get("approvedOn"))
            last_com = iso_date(event.get("lastDatePrior"))
            payment = iso_date(event.get("paymentDate"))
            rate = parse_decimal(event.get("rate"))
            related = str(event.get("relatedTo") or "").strip()
            remarks = str(event.get("remarks") or "").strip()
            if rate is None or rate < 0:
                continue
            key = event_key(root, ticker, label, approved, last_com, payment, rate, related)
            rows.append((key, root, ticker, label, approved, last_com, payment, rate, related, remarks, 1 if is_dpa_event(label) else 0, B3_PUBLIC_SOURCE, now))
        conn.execute("DELETE FROM cash_dividends WHERE root=?", (root,))
        conn.executemany(
            """
            INSERT INTO cash_dividends(
              event_id,root,ticker,label,approved_on,last_date_prior,payment_date,rate,
              related_to,remarks,eligible_dpa,source_url,imported_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        conn.execute(
            """
            INSERT INTO dividend_imports(
              root,imported_at,status,message,total_shares,common_shares,preferred_shares,source_url
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(root) DO UPDATE SET
              imported_at=excluded.imported_at,status=excluded.status,message=excluded.message,
              total_shares=excluded.total_shares,common_shares=excluded.common_shares,
              preferred_shares=excluded.preferred_shares,source_url=excluded.source_url
            """,
            (root, now, "OK", f"{len(rows)} evento(s) retornado(s)", total_shares, common_shares, preferred_shares, B3_PUBLIC_SOURCE),
        )
        conn.commit()
        return "OK", len(rows)
    except Exception as exc:
        conn.execute(
            """
            INSERT INTO dividend_imports(root,imported_at,status,message,source_url)
            VALUES(?,?,?,?,?)
            ON CONFLICT(root) DO UPDATE SET
              imported_at=excluded.imported_at,status=excluded.status,
              message=excluded.message,source_url=excluded.source_url
            """,
            (root, now, "ERRO", repr(exc), B3_PUBLIC_SOURCE),
        )
        conn.commit()
        return "ERRO", 0


def replace_payout_criterion(criteria: list[dict], payout: float | None, note: str) -> list[dict]:
    replacement = {
        "name": "Payout",
        "value": "Pendente" if payout is None else fmt_pct(payout),
        "limit": "< 90,00%",
        "status": "PENDENTE" if payout is None else ("APROVADO" if payout < PAYOUT_MAX else "REPROVADO"),
        "note": note if payout is None else "Estimado por DPA 12M × ações totais ÷ lucro líquido LTM.",
        "essential": True,
    }
    result: list[dict] = []
    replaced = False
    for criterion in criteria:
        if norm_text(criterion.get("name")) == "PAYOUT":
            result.append(replacement)
            replaced = True
        else:
            result.append(criterion)
    if not replaced:
        result.append(replacement)
    return result


def recalc_quality(conn: sqlite3.Connection, cnpj: str, payout: float | None, note: str) -> str:
    row = conn.execute("SELECT * FROM fundamentals WHERE cnpj=?", (cnpj,)).fetchone()
    if not row:
        return "PENDENTE FUNDAMENTOS"
    try:
        criteria = json.loads(row["criteria_json"] or "[]")
    except json.JSONDecodeError:
        criteria = []
    criteria = replace_payout_criterion(criteria, payout, note)
    old_reason = str(row["reason"] or "")
    structural_red = any(
        marker in norm_text(old_reason)
        for marker in (
            "PREJUIZO RECORRENTE",
            "DIVIDA EM DETERIORACAO ACELERADA",
            "DETERIORACAO ESTRUTURAL",
        )
    )
    financial = bool(row["is_financial"])
    result = quality_rules.classify_quality(
        criteria,
        structural_red=structural_red,
        sector_pending=financial,
    )
    evaluated = result.approved + result.rejected
    score = round(100 * result.approved / evaluated, 1) if evaluated else None
    reason = quality_rules.quality_reason(
        result,
        structural_red=structural_red,
        sector_pending=financial,
    )
    conn.execute(
        """
        UPDATE fundamentals
           SET payout=?,quality_status=?,quality_score=?,failures=?,pending=?,reason=?,criteria_json=?,updated_at=?
         WHERE cnpj=?
        """,
        (
            payout,
            result.status,
            score,
            result.rejected,
            result.pending,
            reason,
            json.dumps(criteria, ensure_ascii=False, separators=(",", ":")),
            datetime.now(timezone.utc).isoformat(),
            cnpj,
        ),
    )
    return result.status


def historical_dpa_baseline(
    conn: sqlite3.Connection,
    ticker: str,
    quote_day: date,
) -> float | None:
    annual_totals: list[float] = []
    for year in range(quote_day.year - 4, quote_day.year):
        rows = list(
            conn.execute(
                """
                SELECT label,approved_on,last_date_prior,payment_date,rate,related_to,remarks
                  FROM cash_dividends
                 WHERE ticker=? AND eligible_dpa=1
                   AND last_date_prior>=? AND last_date_prior<=?
                """,
                (ticker, f"{year}-01-01", f"{year}-12-31"),
            )
        )
        ordinary = [event for event in deduplicate_events(rows) if not is_explicit_extraordinary(event)]
        total = sum(float(event["rate"] or 0) for event in ordinary)
        if total > 0:
            annual_totals.append(total)
    return median(annual_totals) if len(annual_totals) >= 3 else None


def dividend_integrity(
    normalized_dpa: float,
    extraordinary_dpa: float,
    historical_median: float | None,
) -> tuple[str, bool]:
    if historical_median and normalized_dpa > historical_median * DPA_OUTLIER_MULTIPLE:
        return (
            "PENDENTE — DPA 12M atípico versus histórico; confirmar evento não recorrente",
            False,
        )
    if extraordinary_dpa > 0:
        return (
            "NORMALIZADO — provento extraordinário identificado na descrição pública",
            True,
        )
    return "VALIDADO — sem anomalia mecânica identificada", True


def calculate_metrics(conn: sqlite3.Connection) -> dict[str, int]:
    conn.execute("DELETE FROM dividend_metrics")
    principals = list(conn.execute(
        """
        SELECT u.ticker,u.root,u.cnpj,u.price,u.quote_date,u.eligible,
               f.profit_ltm,f.quality_status,f.is_financial
          FROM universe u
          LEFT JOIN fundamentals f ON f.cnpj=u.cnpj
         WHERE u.principal='SIM'
        """
    ))
    now = datetime.now(timezone.utc).isoformat()
    counters = {"processed": 0, "with_dpa": 0, "bazin_enabled": 0, "bazin_margin_ge_10": 0}
    for row in principals:
        ticker, root, cnpj, price = row["ticker"], row["root"], row["cnpj"] or "", row["price"]
        quote_text = row["quote_date"] or date.today().isoformat()
        try:
            quote_day = date.fromisoformat(quote_text[:10])
        except ValueError:
            quote_day = date.today()
        window_start = quote_day - timedelta(days=365)
        imp = conn.execute("SELECT * FROM dividend_imports WHERE root=?", (root,)).fetchone()
        import_ok = bool(imp and imp["status"] == "OK")
        total_shares = imp["total_shares"] if imp else None
        events = deduplicate_events(list(conn.execute(
            """
            SELECT * FROM cash_dividends
             WHERE ticker=? AND eligible_dpa=1
               AND last_date_prior>=? AND last_date_prior<=?
             ORDER BY last_date_prior DESC,payment_date DESC
            """,
            (ticker, window_start.isoformat(), quote_day.isoformat()),
        )))
        class_mismatch = (
            import_ok
            and not events
            and has_sibling_class_events(
                conn,
                root,
                ticker,
                window_start.isoformat(),
                quote_day.isoformat(),
            )
        )
        dpa_total = (
            sum(float(event["rate"] or 0) for event in events)
            if import_ok and not class_mismatch
            else None
        )
        extraordinary_dpa = (
            sum(float(event["rate"] or 0) for event in events if is_explicit_extraordinary(event))
            if import_ok and not class_mismatch
            else None
        )
        dpa = (
            dpa_total - float(extraordinary_dpa or 0)
            if dpa_total is not None
            else None
        )
        historical_median = (
            historical_dpa_baseline(conn, ticker, quote_day)
            if import_ok and not class_mismatch
            else None
        )
        integrity_status, integrity_ok = (
            dividend_integrity(float(dpa or 0), float(extraordinary_dpa or 0), historical_median)
            if dpa is not None
            else (
                CLASS_MISMATCH_STATUS
                if class_mismatch
                else "PENDENTE — fonte de proventos não conciliada",
                False,
            )
        )
        event_count = len(events) if import_ok else 0
        profit = row["profit_ltm"]
        lpa = float(profit) / int(total_shares) if profit is not None and float(profit) > 0 and total_shares and int(total_shares) > 0 else None
        payout = dpa / lpa if integrity_ok and dpa is not None and lpa and lpa > 0 else None
        dy = dpa / float(price) if dpa is not None and price and float(price) > 0 else None
        dy_total = dpa_total / float(price) if dpa_total is not None and price and float(price) > 0 else None
        if not import_ok:
            payout_note = "Consulta de proventos B3 indisponível para o emissor."
        elif class_mismatch:
            payout_note = integrity_status
        elif not integrity_ok:
            payout_note = integrity_status
        elif not total_shares:
            payout_note = "Quantidade total de ações não foi informada pela consulta B3."
        elif profit is None or float(profit) <= 0:
            payout_note = "Lucro líquido LTM não é positivo; payout não calculável."
        else:
            payout_note = "Dados conciliados com a janela de 12 meses da cotação."
        quality_status = recalc_quality(conn, cnpj, payout, payout_note) if cnpj else "PENDENTE FUNDAMENTOS"
        bazin_ceiling = bazin_margin = None
        bazin_status = "BLOQUEADO"
        if row["eligible"] != "SIM":
            valuation_status = "Valuation bloqueado — fora do filtro de liquidez."
        elif not quality_rules.is_approved(quality_status):
            valuation_status = "Valuation bloqueado pelo filtro de qualidade."
        elif dpa is None or payout is None:
            valuation_status = "Valuation bloqueado — DPA ou payout não conciliado."
        elif not price or float(price) <= 0:
            valuation_status = "Valuation bloqueado — fechamento B3 indisponível."
        elif dpa == 0:
            bazin_status = "SEM PROVENTOS 12M — NÃO APLICÁVEL"
            valuation_status = "NÃO APLICÁVEL — sem dividendos/JCP validados nos 12M"
        else:
            bazin_ceiling = dpa / BAZIN_YIELD
            bazin_margin = bazin_ceiling / float(price) - 1
            bazin_status = quality_rules.bazin_band(bazin_margin)
            valuation_status = "CALCULADO — Bazin com último fechamento oficial B3"
            counters["bazin_enabled"] += 1
            if bazin_margin >= 0.10:
                counters["bazin_margin_ge_10"] += 1
        conn.execute(
            """
            INSERT INTO dividend_metrics(
              ticker,root,cnpj,quote_date,window_start,dpa_12m,dpa_total_12m,
              extraordinary_dpa_12m,dividend_yield,dividend_yield_total,
              historical_dpa_median,dividend_integrity_status,total_shares,lpa,payout,
              bazin_ceiling,bazin_margin,bazin_status,valuation_status,events_count,source_url,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ticker, root, cnpj, quote_day.isoformat(), window_start.isoformat(),
                dpa, dpa_total, extraordinary_dpa, dy, dy_total, historical_median,
                integrity_status, total_shares, lpa, payout, bazin_ceiling,
                bazin_margin, bazin_status, valuation_status, event_count,
                B3_PUBLIC_SOURCE, now,
            ),
        )
        counters["processed"] += 1
        if dpa is not None:
            counters["with_dpa"] += 1
    conn.commit()
    return counters


def event_payload(conn: sqlite3.Connection, ticker: str, start: str, end: str) -> list[dict]:
    rows = deduplicate_events(list(conn.execute(
        """
        SELECT label,approved_on,last_date_prior,payment_date,rate,related_to,remarks
          FROM cash_dividends
         WHERE ticker=? AND eligible_dpa=1
           AND last_date_prior>=? AND last_date_prior<=?
         ORDER BY last_date_prior DESC,payment_date DESC
        """,
        (ticker, start, end),
    )))
    return [{
        "tipo": row["label"],
        "dataCom": row["last_date_prior"],
        "pagamento": row["payment_date"],
        "valor": fmt_money(row["rate"], 6),
        "valorRaw": row["rate"],
        "periodo": row["related_to"],
        "observacoes": row["remarks"],
        "extraordinarioExplicito": is_explicit_extraordinary(row),
    } for row in rows]


def financial_ratio(value: float | None, maximum: float) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    return numeric if 0.0 <= numeric <= maximum else None


def enrich_regulatory_data(conn: sqlite3.Connection, payload: dict) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='financial_regulatory'"
    ).fetchone()
    if not exists:
        return
    regulatory = {
        row["ticker"]: row
        for row in conn.execute("SELECT * FROM financial_regulatory")
    }
    for item in payload.get("items", []):
        if not item.get("financeira"):
            continue
        row = regulatory.get(item.get("ticker"))
        if not row:
            continue
        ratios = {
            "basel": financial_ratio(row["basel"], 1.0),
            "capital_principal": financial_ratio(row["capital_principal"], 1.0),
            "tier1": financial_ratio(row["tier1"], 1.0),
            "efficiency": financial_ratio(row["efficiency"], 2.0),
            "npl": financial_ratio(row["npl"], 1.0),
            "coverage": financial_ratio(row["coverage"], 20.0),
        }
        item.update(
            {
                "ifdataPeriodo": str(row["period"] or ""),
                "ifdataInstituicao": row["institution_name"],
                "ifdataStatus": row["status"],
                "basileia": fmt_pct(ratios["basel"]),
                "basileiaRaw": ratios["basel"],
                "capitalPrincipal": fmt_pct(ratios["capital_principal"]),
                "capitalPrincipalRaw": ratios["capital_principal"],
                "nivel1": fmt_pct(ratios["tier1"]),
                "nivel1Raw": ratios["tier1"],
                "eficiencia": fmt_pct(ratios["efficiency"]),
                "eficienciaRaw": ratios["efficiency"],
                "inadimplencia": fmt_pct(ratios["npl"]),
                "inadimplenciaRaw": ratios["npl"],
                "cobertura": fmt_pct(ratios["coverage"]),
                "coberturaRaw": ratios["coverage"],
                "fonteRegulatoria": row["source_url"],
            }
        )


def enrich_site(conn: sqlite3.Connection, summary: dict[str, int], import_summary: dict[str, int]) -> dict:
    payload = app.build_site(conn)
    enrich_regulatory_data(conn, payload)
    metrics = {row["ticker"]: row for row in conn.execute("SELECT * FROM dividend_metrics")}
    for item in payload.get("items", []):
        metric = metrics.get(item["ticker"])
        if not metric:
            continue
        item.update({
            "dpa12m": fmt_money(metric["dpa_12m"], 4) if metric["dpa_12m"] is not None else "Pendente",
            "dpa12mRaw": metric["dpa_12m"],
            "dpaTotal12m": fmt_money(metric["dpa_total_12m"], 4) if metric["dpa_total_12m"] is not None else "Pendente",
            "dpaTotal12mRaw": metric["dpa_total_12m"],
            "dpaExtraordinario12m": fmt_money(metric["extraordinary_dpa_12m"], 4) if metric["extraordinary_dpa_12m"] else "R$ 0,0000",
            "dpaExtraordinario12mRaw": metric["extraordinary_dpa_12m"],
            "dy12m": fmt_pct(metric["dividend_yield"]),
            "dy12mRaw": metric["dividend_yield"],
            "dyTotal12m": fmt_pct(metric["dividend_yield_total"]),
            "dyTotal12mRaw": metric["dividend_yield_total"],
            "medianaDpaHistorico": fmt_money(metric["historical_dpa_median"], 4) if metric["historical_dpa_median"] is not None else "Pendente",
            "medianaDpaHistoricoRaw": metric["historical_dpa_median"],
            "integridadeProventos": metric["dividend_integrity_status"],
            "acoesTotais": metric["total_shares"],
            "lpa": fmt_money(metric["lpa"], 4) if metric["lpa"] is not None else "Pendente",
            "lpaRaw": metric["lpa"],
            "payout": fmt_pct(metric["payout"]) if metric["payout"] is not None else "Pendente",
            "payoutRaw": metric["payout"],
            "precoTetoBazin": fmt_money(metric["bazin_ceiling"]) if metric["bazin_ceiling"] is not None else "Bloqueado",
            "precoTetoBazinRaw": metric["bazin_ceiling"],
            "margemBazin": fmt_pct(metric["bazin_margin"], True) if metric["bazin_margin"] is not None else "—",
            "margemBazinRaw": metric["bazin_margin"],
            "statusBazin": metric["bazin_status"],
            "valuationStatus": metric["valuation_status"],
            "janelaProventosInicio": metric["window_start"],
            "janelaProventosFim": metric["quote_date"],
            "quantidadeEventos12m": metric["events_count"],
            "fonteProventos": metric["source_url"],
            "eventosProventos": event_payload(conn, item["ticker"], metric["window_start"], metric["quote_date"]),
        })
    payload["qualityApproved"] = sum(item.get("filtroQualidadeOriginal") == "APROVADA NO FILTRO" and item.get("elegivelInicial") == "SIM" for item in payload.get("items", []))
    payload["qualityRejected"] = sum(item.get("filtroQualidadeOriginal") == "REPROVADA NO FILTRO" and item.get("elegivelInicial") == "SIM" for item in payload.get("items", []))
    payload["qualityPartialApproved"] = 0
    payload["redAlerts"] = sum(item.get("filtroQualidadeOriginal") == "ALERTA VERMELHO" and item.get("elegivelInicial") == "SIM" for item in payload.get("items", []))
    payload["pendingFundamentals"] = sum(str(item.get("filtroQualidadeOriginal", "")).startswith("PENDENTE") and item.get("elegivelInicial") == "SIM" for item in payload.get("items", []))
    payload["bazinEnabled"] = summary["bazin_enabled"]
    payload["bazinMarginGe10"] = summary["bazin_margin_ge_10"]
    payload.pop("bazinAttractive", None)
    payload["dividendImportsOk"] = import_summary["ok"] + import_summary["cache"]
    payload["dividendImportsError"] = import_summary["error"]
    payload["dividendsUpdatedAt"] = datetime.now(timezone.utc).isoformat()
    payload["dividendsReferenceDate"] = payload.get("latestQuoteDate", "")
    payload["methodology"]["quality"] = (
        "ROE médio 5A > 12%; CAGR do lucro > 0%; DL/EBITDA < 3x; margem estável/crescente; "
        "lucro positivo em ao menos 4 de 5 anos; payout normalizado 12M < 90%. "
        "Uma reprovação já reprova; duas ou deterioração estrutural geram Alerta Vermelho; "
        "qualquer dado essencial ausente mantém a empresa pendente."
    )
    payload["methodology"]["valuation"] = (
        "Bazin somente após o filtro de qualidade e com DPA/payout conciliados. "
        "DPA 12M usa dividendos e JCP deduplicados cuja última data COM está entre a data-base "
        "de mercado e os 365 dias anteriores. Eventos explicitamente extraordinários são separados; "
        "um DPA atípico sem documentação bloqueia o cálculo. Preço-teto = DPA normalizado / 7,75%; "
        "margem = preço-teto / último fechamento B3 − 1."
    )
    payload["disclaimer"] = (
        "Análise educacional baseada em dados públicos B3/CVM. Não constitui recomendação de compra ou venda. "
        "Margens de valuation são resultados matemáticos, não sinais de compra."
    )
    (app.DOCS_DIR / "data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    status_path = app.DOCS_DIR / "status.json"
    status = {}
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            status = {}
    status.update({
        "status": "OK" if import_summary["error"] == 0 else "PARCIAL",
        "dividendsUpdatedAt": payload["dividendsUpdatedAt"],
        "dividendsReferenceDate": payload["dividendsReferenceDate"],
        "dividendImportsOk": payload["dividendImportsOk"],
        "dividendImportsError": payload["dividendImportsError"],
        "bazinEnabled": payload["bazinEnabled"],
        "bazinMarginGe10": payload["bazinMarginGe10"],
    })
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def run() -> int:
    conn = app.connect()
    ensure_schema(conn)
    try:
        roots = [row[0] for row in conn.execute("SELECT DISTINCT root FROM universe WHERE principal='SIM' AND root<>'' ORDER BY root")]
        import_summary = {"ok": 0, "cache": 0, "error": 0, "events": 0}
        status_bucket = {"OK": "ok", "CACHE": "cache", "ERRO": "error"}
        for index, root in enumerate(roots, 1):
            status, count = import_root(conn, root)
            bucket = status_bucket.get(status, "error")
            import_summary[bucket] += 1
            import_summary["events"] += count
            if status == "ERRO":
                detail = conn.execute("SELECT message FROM dividend_imports WHERE root=?", (root,)).fetchone()
                print(f"Aviso proventos {root}: {detail['message'] if detail else 'erro sem detalhe'}")
            if index % 25 == 0:
                print(f"Proventos B3: {index}/{len(roots)} emissores")
        metric_summary = calculate_metrics(conn)
        run_status = "OK" if import_summary["error"] == 0 else "PARCIAL"
        app.log(conn, run_status, f"Proventos: {import_summary['ok']} novos, {import_summary['cache']} em cache, {import_summary['error']} erros; Bazin liberado para {metric_summary['bazin_enabled']} ações.")
        payload = enrich_site(conn, metric_summary, import_summary)
        print(json.dumps({"status": run_status, "emissores": len(roots), **import_summary, **metric_summary, "data_base": payload.get("latestQuoteDate")}, ensure_ascii=False))
        return 0
    except Exception as exc:
        app.log(conn, "ERRO", f"Proventos/Bazin: {exc!r}")
        print(f"ERRO Proventos/Bazin: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
