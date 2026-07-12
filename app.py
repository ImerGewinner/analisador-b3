from __future__ import annotations

import base64
import csv
import io
import json
import sqlite3
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from fundamentals import (
    FUNDAMENTALS_SOURCE,
    ensure_fundamentals_schema,
    load_and_score_fundamentals,
)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
DB_PATH = DATA_DIR / "analisador.db"
SEED_PATH = DATA_DIR / "universe_seed.csv"
TIMEZONE_LABEL = "America/Manaus"

B3_COMPANIES_API = "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetInitialCompanies/"
B3_COTAHIST_BASE = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/"

FILTERS = {
    "liquidez_minima_20d": 1_000_000.0,
    "pregoes_minimos_20d": 15,
    "max_sessoes_sem_negocio": 5,
    "valor_mercado_minimo": 300_000_000.0,
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS universe (
            ticker TEXT PRIMARY KEY,
            cnpj TEXT,
            cvm_code TEXT,
            root TEXT,
            company TEXT,
            trading_name TEXT,
            segment TEXT,
            listing_date TEXT,
            price REAL,
            variation REAL,
            volume_day REAL,
            avg20 REAL,
            avg60 REAL,
            sessions20 INTEGER,
            sessions60 INTEGER,
            stale_sessions INTEGER,
            principal TEXT,
            passes_liquidity TEXT,
            passes_activity TEXT,
            eligible TEXT,
            reason TEXT,
            quote_date TEXT,
            source TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS history (
            quote_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            close REAL,
            volume REAL,
            trades INTEGER,
            PRIMARY KEY (quote_date, ticker)
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL
        );
        """
    )
    ensure_column(conn, "universe", "cnpj", "TEXT")
    ensure_column(conn, "universe", "cvm_code", "TEXT")
    ensure_fundamentals_schema(conn)
    conn.commit()
    return conn


def log(conn: sqlite3.Connection, status: str, message: str) -> None:
    conn.execute(
        "INSERT INTO runs(run_at,status,message) VALUES(?,?,?)",
        (datetime.now(timezone.utc).isoformat(), status, message),
    )
    conn.commit()


def normalize_cvm_code(value: str) -> str:
    value = str(value or "").strip()
    if value.endswith(".0"):
        value = value[:-2]
    return value


def load_seed() -> tuple[dict[str, dict], dict[str, dict]]:
    by_ticker: dict[str, dict] = {}
    by_root: dict[str, dict] = {}
    if not SEED_PATH.exists():
        return by_ticker, by_root
    with SEED_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            ticker = str(row.get("ticker") or "").upper().strip()
            root = str(row.get("root") or "").upper().strip()
            if not ticker or not root:
                continue
            item = {
                "ticker": ticker,
                "root": root,
                "cnpj": str(row.get("cnpj") or "").strip(),
                "cvm_code": normalize_cvm_code(row.get("cvm_code") or ""),
                "company": str(row.get("company") or "").strip(),
                "trading_name": str(row.get("trading_name") or "").strip(),
            }
            by_ticker[ticker] = item
            by_root[root] = item
    return by_ticker, by_root


def b3_json(url: str, payload: dict) -> dict:
    token = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    response = requests.get(url + token, timeout=60)
    response.raise_for_status()
    return response.json()


def fetch_companies() -> dict[str, dict]:
    companies: dict[str, dict] = {}
    page = 1
    total_pages = 1
    while page <= total_pages:
        body = b3_json(
            B3_COMPANIES_API,
            {"language": "pt-br", "pageNumber": page, "pageSize": 120},
        )
        total_pages = int((body.get("page") or {}).get("totalPages") or total_pages)
        for row in body.get("results") or []:
            if str(row.get("status")) != "A" or str(row.get("type")) != "1":
                continue
            if str(row.get("typeBDR") or "").strip():
                continue
            root = str(row.get("issuingCompany") or "").upper().strip()
            if not root:
                continue
            score = (0 if str(row.get("marketIndicator")) == "99" else 4) + (
                0 if str(row.get("dateListing")) == "31/12/9999" else 2
            )
            current = companies.get(root)
            if not current or score > current["_score"]:
                companies[root] = {
                    "company": str(row.get("companyName") or "").strip(),
                    "trading_name": str(row.get("tradingName") or "").strip(),
                    "segment": str(row.get("segment") or "").strip(),
                    "listing_date": str(row.get("dateListing") or "").strip(),
                    "_score": score,
                }
        page += 1
    return companies


def fixed_number(value: str, decimals: int = 2) -> float:
    digits = value.strip()
    if not digits:
        return 0.0
    return int(digits) / (10**decimals)


def parse_cotahist(text: str) -> list[dict]:
    result: list[dict] = []
    for line in text.splitlines():
        if len(line) < 188 or line[:2] != "01" or line[24:27] != "010":
            continue
        ticker = line[12:24].strip().upper()
        if len(ticker) < 5:
            continue
        suffix = ticker[4:]
        if suffix not in {"3", "4", "5", "6", "7", "8", "11"}:
            continue
        ymd = line[2:10]
        result.append(
            {
                "quote_date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}",
                "ticker": ticker,
                "company": line[27:39].strip(),
                "close": fixed_number(line[108:121]),
                "trades": int(line[147:152].strip() or 0),
                "volume": fixed_number(line[170:188]),
            }
        )
    return result


def fetch_cotahist(day: date) -> list[dict] | None:
    stamp = day.strftime("%d%m%Y")
    url = f"{B3_COTAHIST_BASE}COTAHIST_D{stamp}.ZIP"
    response = requests.get(url, timeout=90, allow_redirects=True)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        name = next((n for n in names if n.upper().endswith(".TXT")), names[0])
        text = archive.read(name).decode("latin-1")
    rows = parse_cotahist(text)
    return rows or None


def fetch_latest_cotahist(max_days: int = 15) -> tuple[date, list[dict]]:
    base = date.today()
    for offset in range(max_days + 1):
        day = base - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        rows = fetch_cotahist(day)
        if rows:
            return day, rows
    raise RuntimeError("Nenhum COTAHIST diário foi localizado nos últimos dias.")


def history_days(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(DISTINCT quote_date) FROM history").fetchone()[0])


def save_history(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO history(quote_date,ticker,close,volume,trades)
        VALUES(:quote_date,:ticker,:close,:volume,:trades)
        ON CONFLICT(quote_date,ticker) DO UPDATE SET
          close=excluded.close, volume=excluded.volume, trades=excluded.trades
        """,
        rows,
    )
    conn.commit()


def backfill_history(conn: sqlite3.Connection, target_days: int = 65) -> None:
    if history_days(conn) >= 60:
        return
    collected = history_days(conn)
    cursor = date.today()
    attempts = 0
    while collected < target_days and attempts < 120:
        if cursor.weekday() < 5:
            try:
                rows = fetch_cotahist(cursor)
                if rows:
                    save_history(conn, rows)
                    collected = history_days(conn)
                    print(f"Histórico: {collected} pregões")
            except requests.HTTPError as exc:
                print(f"Aviso histórico {cursor}: {exc}")
        cursor -= timedelta(days=1)
        attempts += 1


def avg(values: list[float], denominator: int) -> float:
    return sum(values) / denominator if denominator else 0.0


def update_universe(
    conn: sqlite3.Connection,
    companies: dict[str, dict],
    quote_day: date,
    quotes: list[dict],
) -> int:
    seed_by_ticker, seed_by_root = load_seed()
    previous = {
        row["ticker"]: row["close"]
        for row in conn.execute(
            """
            SELECT h.ticker,h.close
            FROM history h
            JOIN (
              SELECT ticker,MAX(quote_date) quote_date
              FROM history WHERE quote_date < ? GROUP BY ticker
            ) x ON x.ticker=h.ticker AND x.quote_date=h.quote_date
            """,
            (quote_day.isoformat(),),
        )
    }
    by_ticker = {row["ticker"]: row for row in quotes}
    now = datetime.now(timezone.utc).isoformat()
    changed = 0
    for ticker, quote in by_ticker.items():
        root = ticker[:4]
        company = companies.get(root) or {}
        seed = seed_by_ticker.get(ticker) or seed_by_root.get(root) or {}
        if not company and not seed:
            continue
        hist = list(
            conn.execute(
                "SELECT quote_date,volume FROM history WHERE ticker=? ORDER BY quote_date DESC LIMIT 60",
                (ticker,),
            )
        )
        volumes = [float(row["volume"] or 0) for row in reversed(hist)]
        last20 = volumes[-20:]
        last60 = volumes[-60:]
        stale = len(last60)
        for idx, value in enumerate(reversed(last60)):
            if value > 0:
                stale = idx
                break
        avg20 = avg(last20, 20)
        avg60 = avg(last60, 60)
        sessions20 = sum(v > 0 for v in last20)
        sessions60 = sum(v > 0 for v in last60)
        passes_liquidity = "SIM" if avg20 >= FILTERS["liquidez_minima_20d"] else "NÃO"
        passes_activity = (
            "SIM"
            if sessions20 >= FILTERS["pregoes_minimos_20d"]
            and stale <= FILTERS["max_sessoes_sem_negocio"]
            else "NÃO"
        )
        eligible = "SIM" if passes_liquidity == "SIM" and passes_activity == "SIM" else "NÃO"
        reason = (
            "Passou no filtro inicial"
            if eligible == "SIM"
            else "Liquidez insuficiente"
            if passes_liquidity != "SIM"
            else "Negociação pouco frequente"
        )
        prev = previous.get(ticker)
        variation = quote["close"] / prev - 1 if prev and prev > 0 else None
        conn.execute(
            """
            INSERT INTO universe(
              ticker,cnpj,cvm_code,root,company,trading_name,segment,listing_date,price,variation,
              volume_day,avg20,avg60,sessions20,sessions60,stale_sessions,principal,
              passes_liquidity,passes_activity,eligible,reason,quote_date,source,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticker) DO UPDATE SET
              cnpj=excluded.cnpj,cvm_code=excluded.cvm_code,root=excluded.root,
              company=excluded.company,trading_name=excluded.trading_name,
              segment=excluded.segment,listing_date=excluded.listing_date,price=excluded.price,
              variation=excluded.variation,volume_day=excluded.volume_day,avg20=excluded.avg20,
              avg60=excluded.avg60,sessions20=excluded.sessions20,sessions60=excluded.sessions60,
              stale_sessions=excluded.stale_sessions,passes_liquidity=excluded.passes_liquidity,
              passes_activity=excluded.passes_activity,eligible=excluded.eligible,
              reason=excluded.reason,quote_date=excluded.quote_date,source=excluded.source,
              updated_at=excluded.updated_at
            """,
            (
                ticker,
                seed.get("cnpj", ""),
                seed.get("cvm_code", ""),
                root,
                company.get("company") or seed.get("company") or quote["company"],
                company.get("trading_name") or seed.get("trading_name") or "",
                company.get("segment") or "",
                company.get("listing_date") or "",
                quote["close"],
                variation,
                quote["volume"],
                avg20,
                avg60,
                sessions20,
                sessions60,
                stale,
                "NÃO",
                passes_liquidity,
                passes_activity,
                eligible,
                reason,
                quote_day.isoformat(),
                "B3 COTAHIST — fechamento oficial",
                now,
            ),
        )
        changed += 1

    roots = [row[0] for row in conn.execute("SELECT DISTINCT root FROM universe")]
    for root in roots:
        rows = list(
            conn.execute(
                "SELECT ticker,avg20 FROM universe WHERE root=? ORDER BY avg20 DESC,ticker",
                (root,),
            )
        )
        if not rows:
            continue
        conn.execute("UPDATE universe SET principal='NÃO' WHERE root=?", (root,))
        conn.execute("UPDATE universe SET principal='SIM' WHERE ticker=?", (rows[0]["ticker"],))
    conn.commit()
    return changed


def fmt_money(value: float | None) -> str:
    if value is None:
        return ""
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return ""
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value*100:.2f}%".replace(".", ",")


def fmt_multiple(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.2f}x".replace(".", ",")


def build_site(conn: sqlite3.Connection) -> dict:
    rows = list(
        conn.execute(
            """
            SELECT
                u.*,
                f.reference_date fundamentals_ref,
                f.origin fundamentals_origin,
                f.revenue_ltm,
                f.profit_ltm,
                f.ebitda_ltm,
                f.equity,
                f.cash,
                f.debt,
                f.net_debt,
                f.roe_avg_5y,
                f.profit_cagr_5y,
                f.net_margin_latest,
                f.margin_trend,
                f.positive_profits_5y,
                f.net_debt_ebitda,
                f.payout,
                f.is_financial,
                f.quality_status,
                f.quality_score,
                f.failures,
                f.pending,
                f.reason quality_reason,
                f.criteria_json,
                f.source_url fundamentals_source
            FROM universe u
            LEFT JOIN fundamentals f ON f.cnpj=u.cnpj
            WHERE u.principal='SIM'
            ORDER BY
                CASE WHEN u.eligible='SIM' THEN 0 ELSE 1 END,
                CASE
                    WHEN f.quality_status LIKE 'APROVADA%' THEN 0
                    WHEN f.quality_status='ALERTA VERMELHO' THEN 1
                    ELSE 2
                END,
                u.avg20 DESC,
                u.ticker
            """
        )
    )

    items = []
    for row in rows:
        criteria = []
        try:
            criteria = json.loads(row["criteria_json"] or "[]")
        except json.JSONDecodeError:
            criteria = []
        items.append(
            {
                "ticker": row["ticker"],
                "empresa": row["company"],
                "segmento": row["segment"],
                "preco": fmt_money(row["price"]),
                "precoRaw": row["price"],
                "variacao": fmt_pct(row["variation"], signed=True),
                "volume20": fmt_money(row["avg20"]),
                "pregoes20": row["sessions20"],
                "elegivelInicial": row["eligible"],
                "motivoInicial": row["reason"],
                "statusQualidade": row["quality_status"] or "PENDENTE FUNDAMENTOS",
                "scoreQualidade": row["quality_score"],
                "falhas": row["failures"],
                "pendencias": row["pending"],
                "motivoQualidade": row["quality_reason"] or "DFP/ITR ainda não consolidado.",
                "financeira": bool(row["is_financial"]),
                "roe5a": fmt_pct(row["roe_avg_5y"]),
                "cagrLucro5a": fmt_pct(row["profit_cagr_5y"]),
                "margemLiquida": fmt_pct(row["net_margin_latest"]),
                "tendenciaMargem": row["margin_trend"] or "",
                "lucrosPositivos5a": row["positive_profits_5y"],
                "dlEbitda": "N/A SETOR" if row["is_financial"] else fmt_multiple(row["net_debt_ebitda"]),
                "receitaLtm": fmt_money(row["revenue_ltm"]),
                "lucroLtm": fmt_money(row["profit_ltm"]),
                "ebitdaLtm": fmt_money(row["ebitda_ltm"]),
                "patrimonio": fmt_money(row["equity"]),
                "dividaLiquida": fmt_money(row["net_debt"]),
                "referenciaFundamentos": row["fundamentals_ref"] or "",
                "origemFundamentos": row["fundamentals_origin"] or "",
                "criterios": criteria,
                "dataCotacao": row["quote_date"],
                "fonteCotacao": row["source"],
                "fonteFundamentos": row["fundamentals_source"] or FUNDAMENTALS_SOURCE,
                "valuationStatus": "BLOQUEADO — aguardando proventos/payout e aprovação no filtro",
            }
        )

    latest_run = conn.execute(
        "SELECT run_at,status,message FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "timezone": TIMEZONE_LABEL,
        "count": len(items),
        "initialEligible": sum(item["elegivelInicial"] == "SIM" for item in items),
        "qualityApproved": sum(item["statusQualidade"] == "APROVADA NO FILTRO" for item in items),
        "qualityPartialApproved": sum(item["statusQualidade"].startswith("APROVADA PARCIAL") for item in items),
        "redAlerts": sum(item["statusQualidade"] == "ALERTA VERMELHO" for item in items),
        "pendingFundamentals": sum(item["statusQualidade"].startswith("PENDENTE") for item in items),
        "latestQuoteDate": max((item["dataCotacao"] for item in items), default=""),
        "latestFundamentalsDate": max((item["referenciaFundamentos"] for item in items), default=""),
        "lastRun": dict(latest_run) if latest_run else None,
        "items": items,
        "methodology": {
            "roeMin": "12,00%",
            "profitGrowthMin": "acima de 0,00%",
            "netDebtEbitdaMax": "3,00x",
            "positiveProfitYears": "mínimo 4 de 5 anos",
            "margin": "estável ou crescente",
            "financialSector": "dívida líquida/EBITDA não aplicada; análise parcial até métricas prudenciais",
        },
        "disclaimer": (
            "Análise educacional baseada em dados públicos B3/CVM. "
            "Não constitui recomendação de compra ou venda. Valuation permanece bloqueado nesta etapa."
        ),
    }
    (DOCS_DIR / "data.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def run() -> int:
    conn = connect()
    try:
        log(conn, "INICIADO", "Atualização de mercado e fundamentos iniciada.")
        companies = fetch_companies()
        backfill_history(conn)
        quote_day, quotes = fetch_latest_cotahist()
        save_history(conn, quotes)
        changed = update_universe(conn, companies, quote_day, quotes)
        issuers = [dict(row) for row in conn.execute(
            "SELECT DISTINCT cnpj,cvm_code,company,segment FROM universe WHERE cnpj<>''"
        )]
        fundamentals_summary = load_and_score_fundamentals(conn, issuers)
        log(
            conn,
            "OK",
            (
                f"Mercado B3: {changed} ativos; base {quote_day}. "
                f"Fundamentos: {fundamentals_summary['processed']} emissores processados, "
                f"{fundamentals_summary['red_alerts']} alertas vermelhos."
            ),
        )
        payload = build_site(conn)
        (DOCS_DIR / "status.json").write_text(
            json.dumps(
                {
                    "status": "OK",
                    "updatedAt": payload["generatedAt"],
                    "quoteDate": payload["latestQuoteDate"],
                    "fundamentalsDate": payload["latestFundamentalsDate"],
                    "companies": payload["count"],
                    "initialEligible": payload["initialEligible"],
                    "qualityApproved": payload["qualityApproved"],
                    "qualityPartialApproved": payload["qualityPartialApproved"],
                    "redAlerts": payload["redAlerts"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(json.dumps({"status": "OK", "ativos": changed, **fundamentals_summary}, ensure_ascii=False))
        return 0
    except Exception as exc:
        log(conn, "ERRO", repr(exc))
        build_site(conn)
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
