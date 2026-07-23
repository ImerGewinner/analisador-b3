from __future__ import annotations

import csv
import io
import json
import math
import re
import sqlite3
import unicodedata
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import app
import dividends
import eps_loader
import quality_rules

BCB_SELIC_SERIES = 432
BCB_SELIC_URL = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{BCB_SELIC_SERIES}/dados"
BCB_SELIC_SOURCE = "Banco Central do Brasil — SGS série 432 (Meta Selic)"
CVM_BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS"
CVM_FCF_SOURCE = "CVM DFP — Demonstração dos Fluxos de Caixa"
WACC = 0.12
G_MAX = 0.06
DCF_HAIRCUT = 0.75
TERMINAL_GROWTH = 0.03
DCF_YEARS = 10


def norm_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().upper()


def number(value: object, scale: object = "") -> float | None:
    raw = str(value or "").strip().replace(" ", "")
    if not raw:
        return None
    try:
        normalized = raw.replace(".", "").replace(",", ".") if "," in raw else raw
        result = float(normalized)
    except ValueError:
        return None
    scale_text = norm_text(scale)
    if "MILHAO" in scale_text:
        result *= 1_000_000
    elif "MIL" in scale_text:
        result *= 1_000
    return result


def fmt_money(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    formatted = f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def fmt_pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.2f}%".replace(".", ",")


def fmt_multiple(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}x".replace(".", ",")


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, connect=4, read=4, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET"}))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "AnalisadorB3Educacional/4.0"
    return s


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS macro_rates(code TEXT PRIMARY KEY,name TEXT,reference_date TEXT,value REAL,unit TEXT,source_url TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS fcf_history(cnpj TEXT,year INTEGER,cfo REAL,capex REAL,fcf REAL,source_file TEXT,updated_at TEXT,PRIMARY KEY(cnpj,year));
        CREATE TABLE IF NOT EXISTS share_history(cnpj TEXT,year INTEGER,share_class TEXT,eps REAL,profit REAL,estimated_shares REAL,source_file TEXT,updated_at TEXT,PRIMARY KEY(cnpj,year,share_class));
        CREATE TABLE IF NOT EXISTS advanced_metrics(
          ticker TEXT PRIMARY KEY,pe REAL,sector_pe_median REAL,pe_vs_sector REAL,sector_pe_status TEXT,
          fcf_avg_3y REAL,dcf_growth REAL,dcf_fair_price REAL,dcf_margin REAL,dcf_status TEXT,
          dividend_years_5y INTEGER,dividend_streak_5y INTEGER,dpa_cagr_5y REAL,
          dilution_5y REAL,dilution_status TEXT,selic REAL,dy_selic_spread REAL,
          classification TEXT,classification_reason TEXT,source_url TEXT,updated_at TEXT
        );
        """
    )
    app.ensure_column(conn, "advanced_metrics", "fcf_cagr_3y", "REAL")
    app.ensure_column(conn, "advanced_metrics", "dcf_enterprise_value", "REAL")
    app.ensure_column(conn, "advanced_metrics", "dcf_equity_value", "REAL")
    app.ensure_column(conn, "advanced_metrics", "dcf_intrinsic_price", "REAL")
    conn.commit()


def fetch_selic(conn: sqlite3.Connection) -> tuple[float | None, str]:
    end = date.today()
    start = end - timedelta(days=45)
    params = {"formato": "json", "dataInicial": start.strftime("%d/%m/%Y"), "dataFinal": end.strftime("%d/%m/%Y")}
    try:
        response = session().get(BCB_SELIC_URL, params=params, timeout=60)
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("SGS 432 não retornou valores")
        last = rows[-1]
        value = float(str(last["valor"]).replace(",", ".")) / 100.0
        raw = str(last.get("data") or "")
        ref = f"{raw[6:10]}-{raw[3:5]}-{raw[0:2]}" if re.fullmatch(r"\d{2}/\d{2}/\d{4}", raw) else raw
        conn.execute(
            """INSERT INTO macro_rates(code,name,reference_date,value,unit,source_url,updated_at) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET name=excluded.name,reference_date=excluded.reference_date,value=excluded.value,unit=excluded.unit,source_url=excluded.source_url,updated_at=excluded.updated_at""",
            (str(BCB_SELIC_SERIES), "Meta Selic", ref, value, "% a.a.", BCB_SELIC_URL, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return value, ref
    except Exception as exc:
        prior = conn.execute("SELECT value,reference_date FROM macro_rates WHERE code=?", (str(BCB_SELIC_SERIES),)).fetchone()
        print(f"Aviso Selic: {exc}")
        return ((float(prior["value"]), str(prior["reference_date"])) if prior else (None, ""))


def dfp_url(year: int) -> str:
    return f"{CVM_BASE}/dfp_cia_aberta_{year}.zip"


def dfc_members(archive: zipfile.ZipFile, year: int) -> list[tuple[str, int]]:
    names = {Path(name).name.lower(): name for name in archive.namelist()}
    result: list[tuple[str, int]] = []
    for statement in ("dfc_mi", "dfc_md"):
        for suffix, priority in (("con", 2), ("ind", 1)):
            wanted = f"dfp_cia_aberta_{statement}_{suffix}_{year}.csv"
            if wanted in names:
                result.append((names[wanted], priority))
    return result


def parse_fcf_package(content: bytes, year: int, active: set[str]) -> list[dict]:
    chosen_cfo: dict[str, tuple[int, int, float, str]] = {}
    capex_lines: dict[tuple[str, int, int], dict[str, float]] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = dfc_members(archive, year)
        if not members:
            raise RuntimeError(f"DFP {year} sem DFC")
        for member, priority in members:
            with archive.open(member) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding="latin-1", newline=""), delimiter=";")
                if not reader.fieldnames:
                    continue
                reader.fieldnames = [str(name or "").lstrip("\ufeff").strip() for name in reader.fieldnames]
                for row in reader:
                    cnpj = re.sub(r"\D", "", str(row.get("CNPJ_CIA") or ""))
                    if cnpj not in active or not norm_text(row.get("ORDEM_EXERC")).startswith("ULTIMO"):
                        continue
                    try:
                        version = int(float(str(row.get("VERSAO") or "0").replace(",", ".")))
                    except ValueError:
                        version = 0
                    account = str(row.get("CD_CONTA") or "").strip()
                    desc = norm_text(row.get("DS_CONTA"))
                    value = number(row.get("VL_CONTA"), row.get("ESCALA_MOEDA"))
                    if value is None:
                        continue
                    if account == "6.01" or ("CAIXA LIQUIDO" in desc and "OPERACION" in desc):
                        candidate = (priority, version, value, member)
                        current = chosen_cfo.get(cnpj)
                        if current is None or candidate[:2] > current[:2]:
                            chosen_cfo[cnpj] = candidate
                    is_capex = account.startswith("6.02") and any(term in desc for term in ("IMOBILIZADO", "INTANGIVEL")) and any(term in desc for term in ("AQUISICAO", "ADICAO", "COMPRA"))
                    if is_capex:
                        capex_lines.setdefault((cnpj, priority, version), {})[f"{account}|{desc}"] = abs(value)
    result: list[dict] = []
    for cnpj, (priority, version, cfo, member) in chosen_cfo.items():
        capex_map = capex_lines.get((cnpj, priority, version), {})
        capex = sum(capex_map.values()) if capex_map else None
        result.append({"cnpj": cnpj, "year": year, "cfo": cfo, "capex": capex, "fcf": cfo - capex if capex is not None else None, "source_file": member})
    return result


def import_fcf_history(conn: sqlite3.Connection, active: set[str]) -> int:
    current = date.today().year
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for year in range(current - 3, current):
        response = session().get(dfp_url(year), timeout=180)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        rows = parse_fcf_package(response.content, year, active)
        conn.execute("DELETE FROM fcf_history WHERE year=?", (year,))
        conn.executemany("INSERT INTO fcf_history(cnpj,year,cfo,capex,fcf,source_file,updated_at) VALUES(?,?,?,?,?,?,?)", [(r["cnpj"], year, r["cfo"], r["capex"], r["fcf"], r["source_file"], now) for r in rows])
        inserted += len(rows)
        conn.commit()
    return inserted


def latest_annual_profit(conn: sqlite3.Connection, cnpj: str, year: int) -> float | None:
    row = conn.execute("SELECT profit FROM financial_periods WHERE cnpj=? AND doc_type='DFP' AND year=? ORDER BY date_ref DESC LIMIT 1", (cnpj, year)).fetchone()
    return float(row["profit"]) if row and row["profit"] is not None else None


def import_share_history(conn: sqlite3.Connection, active: set[str]) -> int:
    current = date.today().year
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    conn.execute("DELETE FROM share_history")
    for year in range(current - 5, current):
        rows = eps_loader.load_package("DFP", year, active)
        for row in rows:
            profit = latest_annual_profit(conn, row["cnpj"], year)
            if profit is None or profit <= 0:
                continue
            for share_class, field in (("ON", "eps_on"), ("PN", "eps_pn")):
                eps = row.get(field)
                if eps is None or float(eps) <= 0:
                    continue
                shares = profit / float(eps)
                if not math.isfinite(shares) or shares <= 0:
                    continue
                conn.execute("INSERT OR REPLACE INTO share_history(cnpj,year,share_class,eps,profit,estimated_shares,source_file,updated_at) VALUES(?,?,?,?,?,?,?,?)", (row["cnpj"], year, share_class, float(eps), profit, shares, row.get("source_file", ""), now))
                inserted += 1
    conn.commit()
    return inserted


def share_class_for_ticker(ticker: str) -> str:
    if ticker.endswith("3"):
        return "ON"
    if ticker[-1:] in {"4", "5", "6", "7", "8"}:
        return "PN"
    return ""


def dividend_history(conn: sqlite3.Connection, ticker: str, quote_date: str) -> tuple[int, int, float | None]:
    try:
        quote_year = date.fromisoformat(quote_date[:10]).year
    except ValueError:
        quote_year = date.today().year
    years = list(range(quote_year - 5, quote_year))
    values: dict[int, float] = {}
    for year in years:
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
        ordinary = [
            event
            for event in dividends.deduplicate_events(rows)
            if not dividends.is_explicit_extraordinary(event)
        ]
        values[year] = sum(float(event["rate"] or 0) for event in ordinary)
    paid = sum(value > 0 for value in values.values())
    streak = 0
    for year in reversed(years):
        if values[year] > 0:
            streak += 1
        else:
            break
    first, last = values[years[0]], values[years[-1]]
    cagr = (last / first) ** (1 / 4) - 1 if first > 0 and last > 0 else None
    return paid, streak, cagr


def dilution_metric(conn: sqlite3.Connection, cnpj: str, share_class: str) -> tuple[float | None, str]:
    if not share_class:
        return None, "PENDENTE — Unit ou classe não mapeada"
    rows = conn.execute("SELECT year,estimated_shares FROM share_history WHERE cnpj=? AND share_class=? ORDER BY year", (cnpj, share_class)).fetchall()
    if len(rows) < 2:
        return None, "PENDENTE — histórico de ações insuficiente"
    first = float(rows[0]["estimated_shares"] or 0)
    last = float(rows[-1]["estimated_shares"] or 0)
    if first <= 0 or last <= 0:
        return None, "PENDENTE — base inválida"
    change = last / first - 1
    status = "DILUIÇÃO" if change > 0.05 else ("REDUÇÃO DE AÇÕES" if change < -0.05 else "ESTÁVEL")
    return change, status


def series_cagr(values: list[float]) -> float | None:
    if len(values) < 2 or values[0] <= 0 or values[-1] <= 0:
        return None
    return (values[-1] / values[0]) ** (1 / (len(values) - 1)) - 1


def discounted_fcff_valuation(
    *,
    fcf_base: float,
    growth: float,
    net_debt: float,
    shares: float,
    price: float,
) -> dict[str, float]:
    if fcf_base <= 0 or shares <= 0 or price <= 0:
        raise ValueError("DCF exige FCFF, ações e fechamento positivos")
    if growth <= -1:
        raise ValueError("crescimento explícito inválido")
    if WACC <= TERMINAL_GROWTH:
        raise ValueError("WACC deve superar o crescimento terminal")

    present_value_explicit = 0.0
    fcff_year = fcf_base
    for year in range(1, DCF_YEARS + 1):
        fcff_year *= 1 + growth
        present_value_explicit += fcff_year / ((1 + WACC) ** year)

    terminal_value_year_10 = (
        fcff_year * (1 + TERMINAL_GROWTH) / (WACC - TERMINAL_GROWTH)
    )
    present_value_terminal = terminal_value_year_10 / ((1 + WACC) ** DCF_YEARS)
    enterprise_value = present_value_explicit + present_value_terminal
    equity_value = enterprise_value - net_debt
    intrinsic_price = equity_value / shares
    reference_price = intrinsic_price * DCF_HAIRCUT
    margin = reference_price / price - 1
    return {
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "intrinsic_price": intrinsic_price,
        "reference_price": reference_price,
        "margin": margin,
        "present_value_explicit": present_value_explicit,
        "present_value_terminal": present_value_terminal,
    }


def classification_for(row: sqlite3.Row, advanced: dict) -> tuple[str, str]:
    if "eligible" in row.keys() and str(row["eligible"] or "").upper() != "SIM":
        return quality_rules.NOT_CLASSIFIED, "A ação ficou fora do filtro mínimo de liquidez."
    quality = str(row["quality_status"] or "")
    blocked = quality_rules.blocked_classification(quality)
    if blocked:
        return blocked
    growth = row["profit_cagr_5y"]
    bazin_margin = row["bazin_margin"]
    dcf_margin = advanced.get("dcf_margin")
    margins = [
        float(value)
        for value in (bazin_margin, dcf_margin)
        if value is not None
    ]
    growth_label = "positivo" if growth is not None and float(growth) > 0 else "pendente"
    if not margins:
        valuation_label = "não calculado"
    elif any(value >= 0 for value in margins):
        valuation_label = "margem não negativa"
    else:
        valuation_label = "margem negativa"
    return (
        f"Qualidade: aprovada | Crescimento: {growth_label} | Valuation: {valuation_label}",
        "Resumo factual dos três pilares; não representa recomendação nem sinal de compra.",
    )


def compute_metrics(conn: sqlite3.Connection, selic: float | None) -> dict[str, int]:
    rows = conn.execute(
        """SELECT u.ticker,u.cnpj,u.segment,u.price,u.quote_date,u.eligible,
                  f.roe_avg_5y,f.profit_cagr_5y,f.margin_trend,f.quality_status,f.is_financial,f.payout,f.net_debt,
                  dm.lpa,dm.dividend_yield,dm.total_shares,dm.bazin_margin,dm.valuation_status
             FROM universe u LEFT JOIN fundamentals f ON f.cnpj=u.cnpj
             LEFT JOIN dividend_metrics dm ON dm.ticker=u.ticker WHERE u.principal='SIM'"""
    ).fetchall()
    pe_by_ticker: dict[str, float | None] = {}
    sector_values: dict[str, list[float]] = {}
    for row in rows:
        pe = float(row["price"]) / float(row["lpa"]) if row["price"] and row["lpa"] and float(row["lpa"]) > 0 else None
        pe_by_ticker[row["ticker"]] = pe
        if pe is not None and 0 < pe < 200 and row["segment"]:
            sector_values.setdefault(str(row["segment"]), []).append(pe)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM advanced_metrics")
    counters = {"processed": 0, "dcf_enabled": 0, "dcf_margin_ge_10": 0, "dividend_consistent": 0}
    for row in rows:
        ticker = str(row["ticker"])
        cnpj = str(row["cnpj"] or "")
        pe = pe_by_ticker[ticker]
        peers = sector_values.get(str(row["segment"] or ""), [])
        sector_median = median(peers) if len(peers) >= 3 else None
        pe_vs_sector = pe / sector_median - 1 if pe is not None and sector_median else None
        if pe is None:
            pe_status = "PENDENTE"
        elif sector_median is None:
            pe_status = "SETOR SEM AMOSTRA SUFICIENTE"
        elif pe_vs_sector <= -0.15:
            pe_status = "ABAIXO DO SETOR"
        elif pe_vs_sector >= 0.15:
            pe_status = "ACIMA DO SETOR"
        else:
            pe_status = "PRÓXIMO AO SETOR"
        fcf_rows = conn.execute(
            "SELECT year,fcf FROM fcf_history WHERE cnpj=? AND fcf IS NOT NULL ORDER BY year DESC LIMIT 3",
            (cnpj,),
        ).fetchall()
        fcf_rows = list(reversed(fcf_rows))
        fcf_values = [float(item["fcf"]) for item in fcf_rows if item["fcf"] is not None]
        fcf_years = [int(item["year"]) for item in fcf_rows]
        complete_fcf = len(fcf_years) == 3 and max(fcf_years) - min(fcf_years) == 2
        fcf_avg = mean(fcf_values) if complete_fcf else None
        fcf_cagr = series_cagr(fcf_values) if complete_fcf else None
        dcf_growth = dcf_fair = dcf_margin = None
        dcf_enterprise = dcf_equity = dcf_intrinsic = None
        quality = str(row["quality_status"] or "")
        if str(row["eligible"] or "").upper() != "SIM":
            dcf_status = "Valuation bloqueado — fora do filtro de liquidez."
        elif bool(row["is_financial"]):
            dcf_status = "NÃO APLICÁVEL — setor financeiro"
        elif not quality_rules.is_approved(quality):
            dcf_status = "Valuation bloqueado pelo filtro de qualidade."
        elif fcf_avg is None or fcf_avg <= 0:
            dcf_status = "PENDENTE — exige três exercícios completos e positivos de FCFF"
        elif fcf_cagr is None:
            dcf_status = "PENDENTE — CAGR do FCFF 3A não calculável"
        elif row["profit_cagr_5y"] is None:
            dcf_status = "PENDENTE — CAGR do lucro 5A não calculável"
        elif row["net_debt"] is None:
            dcf_status = "PENDENTE — dívida líquida não conciliada"
        elif not row["total_shares"] or float(row["total_shares"]) <= 0:
            dcf_status = "PENDENTE — quantidade de ações indisponível"
        elif not row["price"] or float(row["price"]) <= 0:
            dcf_status = "PENDENTE — fechamento B3 indisponível"
        else:
            dcf_growth = min(fcf_cagr, float(row["profit_cagr_5y"]), G_MAX)
            try:
                valuation = discounted_fcff_valuation(
                    fcf_base=fcf_avg,
                    growth=dcf_growth,
                    net_debt=float(row["net_debt"]),
                    shares=float(row["total_shares"]),
                    price=float(row["price"]),
                )
                dcf_enterprise = valuation["enterprise_value"]
                dcf_equity = valuation["equity_value"]
                dcf_intrinsic = valuation["intrinsic_price"]
                dcf_fair = valuation["reference_price"]
                dcf_margin = valuation["margin"]
                dcf_status = "CALCULADO — DCF FCFF descontado"
                counters["dcf_enabled"] += 1
                if dcf_margin >= 0.10:
                    counters["dcf_margin_ge_10"] += 1
            except ValueError as exc:
                dcf_status = f"PENDENTE — {exc}"
        dividend_years, dividend_streak, dpa_cagr = dividend_history(conn, ticker, str(row["quote_date"] or ""))
        if dividend_years >= 5:
            counters["dividend_consistent"] += 1
        dilution, dilution_status = dilution_metric(conn, cnpj, share_class_for_ticker(ticker))
        spread = float(row["dividend_yield"]) - selic if row["dividend_yield"] is not None and selic is not None else None
        classification, reason = classification_for(row, {"dcf_margin": dcf_margin, "dividend_years_5y": dividend_years})
        conn.execute(
            """INSERT INTO advanced_metrics(
               ticker,pe,sector_pe_median,pe_vs_sector,sector_pe_status,
               fcf_avg_3y,fcf_cagr_3y,dcf_growth,dcf_enterprise_value,
               dcf_equity_value,dcf_intrinsic_price,dcf_fair_price,dcf_margin,
               dcf_status,dividend_years_5y,dividend_streak_5y,dpa_cagr_5y,
               dilution_5y,dilution_status,selic,dy_selic_spread,classification,
               classification_reason,source_url,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ticker, pe, sector_median, pe_vs_sector, pe_status, fcf_avg,
                fcf_cagr, dcf_growth, dcf_enterprise, dcf_equity, dcf_intrinsic,
                dcf_fair, dcf_margin, dcf_status, dividend_years,
                dividend_streak, dpa_cagr, dilution, dilution_status, selic,
                spread, classification, reason,
                f"{CVM_FCF_SOURCE}; {BCB_SELIC_SOURCE}", now,
            ),
        )
        counters["processed"] += 1
    conn.commit()
    return counters


def enrich_site(conn: sqlite3.Connection, counters: dict[str, int], selic_ref: str) -> dict:
    path = app.DOCS_DIR / "data.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else app.build_site(conn)
    metrics = {row["ticker"]: row for row in conn.execute("SELECT * FROM advanced_metrics")}
    for item in payload.get("items", []):
        metric = metrics.get(item.get("ticker"))
        if not metric:
            continue
        item.update({
            "pl": fmt_multiple(metric["pe"]), "plRaw": metric["pe"], "plSetor": fmt_multiple(metric["sector_pe_median"]), "plSetorRaw": metric["sector_pe_median"],
            "plVsSetor": fmt_pct(metric["pe_vs_sector"], True), "plVsSetorRaw": metric["pe_vs_sector"], "statusPlSetor": metric["sector_pe_status"],
            "fcfMedio3A": fmt_money(metric["fcf_avg_3y"]), "fcfMedio3ARaw": metric["fcf_avg_3y"],
            "cagrFcf3A": fmt_pct(metric["fcf_cagr_3y"]), "cagrFcf3ARaw": metric["fcf_cagr_3y"],
            "crescimentoDcf": fmt_pct(metric["dcf_growth"]), "crescimentoDcfRaw": metric["dcf_growth"],
            "valorFirmaDcf": fmt_money(metric["dcf_enterprise_value"]), "valorFirmaDcfRaw": metric["dcf_enterprise_value"],
            "valorPatrimonioDcf": fmt_money(metric["dcf_equity_value"]), "valorPatrimonioDcfRaw": metric["dcf_equity_value"],
            "valorIntrinsecoDcf": fmt_money(metric["dcf_intrinsic_price"]), "valorIntrinsecoDcfRaw": metric["dcf_intrinsic_price"],
            "precoJustoDcf": fmt_money(metric["dcf_fair_price"]), "precoJustoDcfRaw": metric["dcf_fair_price"], "margemDcf": fmt_pct(metric["dcf_margin"], True), "margemDcfRaw": metric["dcf_margin"], "statusDcf": metric["dcf_status"],
            "anosDividendos5A": metric["dividend_years_5y"], "sequenciaDividendos5A": metric["dividend_streak_5y"], "cagrDpa5A": fmt_pct(metric["dpa_cagr_5y"]), "cagrDpa5ARaw": metric["dpa_cagr_5y"],
            "diluicao5A": fmt_pct(metric["dilution_5y"], True), "diluicao5ARaw": metric["dilution_5y"], "statusDiluicao": metric["dilution_status"],
            "selic": fmt_pct(metric["selic"]), "selicRaw": metric["selic"], "spreadDySelic": fmt_pct(metric["dy_selic_spread"], True), "spreadDySelicRaw": metric["dy_selic_spread"],
            "classificacao3P": metric["classification"], "justificativaClassificacao": metric["classification_reason"], "fonteAvancada": metric["source_url"],
        })
    selic_row = conn.execute("SELECT * FROM macro_rates WHERE code=?", (str(BCB_SELIC_SERIES),)).fetchone()
    payload["macro"] = {"selic": fmt_pct(selic_row["value"]) if selic_row else "Pendente", "selicRaw": selic_row["value"] if selic_row else None, "referenceDate": selic_ref, "source": BCB_SELIC_SOURCE}
    payload["dcfEnabled"] = counters["dcf_enabled"]
    payload["dcfMarginGe10"] = counters["dcf_margin_ge_10"]
    payload.pop("dcfAttractive", None)
    payload["dividendConsistent5y"] = counters["dividend_consistent"]
    payload["advancedUpdatedAt"] = datetime.now(timezone.utc).isoformat()
    payload.setdefault("methodology", {})["dcf"] = (
        "DCF somente para não financeiras integralmente aprovadas e com três exercícios "
        "completos de FCFF. Projeta e desconta separadamente 10 fluxos; g explícito é o menor "
        "entre CAGR do FCFF, CAGR do lucro e 6%; WACC 12%; crescimento terminal 3%. O valor "
        "terminal é descontado, a dívida líquida é subtraída e a referência por ação recebe "
        "25% de margem de segurança."
    )
    payload["methodology"]["macro"] = "Selic: meta oficial do Copom, série SGS 432 do Banco Central. Spread DY–Selic é apenas comparação de renda corrente, sem equivalência de risco."
    payload["methodology"]["consistency"] = "Dividendos 5A usam anos-calendário completos; diluição é estimada por lucro líquido ÷ LPA básico da DRE CVM e pode ficar pendente para Units."
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    status_path = app.DOCS_DIR / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    status.pop("dcfAttractive", None)
    status.update({"advancedUpdatedAt": payload["advancedUpdatedAt"], "selic": payload["macro"]["selicRaw"], "selicReferenceDate": selic_ref, "dcfEnabled": counters["dcf_enabled"], "dcfMarginGe10": counters["dcf_margin_ge_10"], "dividendConsistent5y": counters["dividend_consistent"]})
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def run() -> int:
    conn = app.connect()
    ensure_schema(conn)
    try:
        active = {row[0] for row in conn.execute("SELECT DISTINCT cnpj FROM universe WHERE cnpj<>''")}
        selic, selic_ref = fetch_selic(conn)
        fcf_rows = import_fcf_history(conn, active)
        share_rows = import_share_history(conn, active)
        counters = compute_metrics(conn, selic)
        payload = enrich_site(conn, counters, selic_ref)
        app.log(conn, "OK", f"Avançado: Selic {payload['macro']['selic']}; FCF {fcf_rows}; histórico de ações {share_rows}; DCF {counters['dcf_enabled']}; dividendos consistentes {counters['dividend_consistent']}.")
        print(json.dumps({"status": "OK", "fcf_rows": fcf_rows, "share_rows": share_rows, **counters}, ensure_ascii=False))
        return 0
    except Exception as exc:
        app.log(conn, "ERRO", f"Análise avançada: {exc!r}")
        print(f"ERRO análise avançada: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
