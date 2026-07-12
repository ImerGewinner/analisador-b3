from __future__ import annotations

import base64
import io
import json
import sqlite3
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
DB_PATH = DATA_DIR / "analisador.db"
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


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS universe (
            ticker TEXT PRIMARY KEY,
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
    return conn


def log(conn: sqlite3.Connection, status: str, message: str) -> None:
    conn.execute(
        "INSERT INTO runs(run_at,status,message) VALUES(?,?,?)",
        (datetime.now(timezone.utc).isoformat(), status, message),
    )
    conn.commit()


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
        company = companies.get(root)
        if not company:
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
              ticker,root,company,trading_name,segment,listing_date,price,variation,
              volume_day,avg20,avg60,sessions20,sessions60,stale_sessions,principal,
              passes_liquidity,passes_activity,eligible,reason,quote_date,source,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticker) DO UPDATE SET
              root=excluded.root,company=excluded.company,trading_name=excluded.trading_name,
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
                root,
                company["company"] or quote["company"],
                company["trading_name"],
                company["segment"],
                company["listing_date"],
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


def fmt_pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value*100:+.2f}%".replace(".", ",")


def build_site(conn: sqlite3.Connection) -> dict:
    rows = list(
        conn.execute(
            """
            SELECT * FROM universe
            WHERE principal='SIM'
            ORDER BY eligible DESC,avg20 DESC,ticker
            """
        )
    )
    items = [
        {
            "ticker": row["ticker"],
            "empresa": row["company"],
            "segmento": row["segment"],
            "preco": fmt_money(row["price"]),
            "variacao": fmt_pct(row["variation"]),
            "volume20": fmt_money(row["avg20"]),
            "pregoes20": row["sessions20"],
            "elegivel": row["eligible"],
            "motivo": row["reason"],
            "data": row["quote_date"],
            "fonte": row["source"],
        }
        for row in rows
    ]
    latest_run = conn.execute(
        "SELECT run_at,status,message FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "timezone": TIMEZONE_LABEL,
        "count": len(items),
        "eligible": sum(item["elegivel"] == "SIM" for item in items),
        "latestQuoteDate": max((item["data"] for item in items), default=""),
        "lastRun": dict(latest_run) if latest_run else None,
        "items": items,
        "disclaimer": "Análise educacional. Não constitui recomendação de compra ou venda.",
    }
    (DOCS_DIR / "data.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def run() -> int:
    conn = connect()
    try:
        log(conn, "INICIADO", "Atualização iniciada.")
        companies = fetch_companies()
        backfill_history(conn)
        quote_day, quotes = fetch_latest_cotahist()
        save_history(conn, quotes)
        changed = update_universe(conn, companies, quote_day, quotes)
        log(conn, "OK", f"Mercado B3 atualizado: {changed} ativos; base {quote_day}.")
        payload = build_site(conn)
        (DOCS_DIR / "status.json").write_text(
            json.dumps(
                {
                    "status": "OK",
                    "updatedAt": payload["generatedAt"],
                    "quoteDate": payload["latestQuoteDate"],
                    "companies": payload["count"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(json.dumps({"status": "OK", "ativos": changed}, ensure_ascii=False))
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
