from __future__ import annotations

import csv
import io
import json
import math
import re
import sqlite3
import unicodedata
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import quality_rules

CVM_BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC"
FUNDAMENTALS_SOURCE = (
    "CVM DFP/ITR — dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/ e ITR/DADOS/"
)
QUALITY = {
    "roe_min": 0.12,
    "cagr_min": 0.0,
    "debt_ebitda_max": 3.0,
    "positive_years_min": 4,
}
FINANCIAL_SEGMENTS = {
    "BANCOS",
    "SEGURADORAS",
    "RESSEGURADORAS",
    "SERVICOS FINANCEIROS DIVERSOS",
}


def norm_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().upper()


def is_financial_company(metadata: dict) -> bool:
    """Classify by B3 segment, avoiding legal-name false positives.

    QUAL3, for example, contains "corretora de seguros" in its legal name but
    belongs to the healthcare segment and must use the non-financial filter.
    """
    segment = norm_text(metadata.get("segment"))
    if segment in FINANCIAL_SEGMENTS:
        return True
    if re.search(
        r"\b(?:SOCIEDADES? DE )?CREDITO E FINANCIAMENTO\b|"
        r"\bINTERMEDIARIOS FINANCEIROS\b|\bPREVIDENCIA\b",
        segment,
    ):
        return True
    if segment:
        return False
    company = norm_text(metadata.get("company"))
    return bool(re.search(r"\bBANCOS?\b|\bSEGURADORAS?\b|\bRESSEGURADORAS?\b", company))


def norm_cnpj(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def iso_date(value: object) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", text):
        return f"{text[6:10]}-{text[3:5]}-{text[0:2]}"
    return text[:10]


def number(value: object, scale: object = "") -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        result = float(raw.replace(".", "").replace(",", "."))
    except ValueError:
        return None
    scale_text = norm_text(scale)
    if "MILHAO" in scale_text:
        result *= 1_000_000
    elif "MIL" in scale_text:
        result *= 1_000
    return result


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_fundamentals_schema(conn: sqlite3.Connection) -> None:
    expected = {
        "financial_periods": {
            "doc_type", "year", "cnpj", "date_ref", "date_start", "revenue", "ebit",
            "profit", "da", "cash", "equity", "debt", "source_file", "imported_at",
        },
        "cvm_imports": {
            "doc_type", "year", "imported_at", "source_url", "row_count", "status", "message",
        },
        "fundamentals": {
            "cnpj", "reference_date", "origin", "revenue_ltm", "profit_ltm", "ebitda_ltm",
            "equity", "cash", "debt", "net_debt", "roe_avg_5y", "profit_cagr_5y",
            "net_margin_latest", "margin_trend", "positive_profits_5y", "net_debt_ebitda",
            "payout", "is_financial", "quality_status", "quality_score", "failures", "pending",
            "reason", "criteria_json", "source_url", "updated_at",
        },
    }
    for table, required in expected.items():
        existing = columns(conn, table)
        if existing and not required.issubset(existing):
            conn.execute(f"DROP TABLE {table}")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS financial_periods(
          doc_type TEXT, year INTEGER, cnpj TEXT, date_ref TEXT, date_start TEXT,
          revenue REAL, ebit REAL, profit REAL, da REAL, cash REAL, equity REAL, debt REAL,
          source_file TEXT, imported_at TEXT,
          PRIMARY KEY(doc_type,year,cnpj,date_ref)
        );
        CREATE INDEX IF NOT EXISTS idx_financial_periods_cnpj
          ON financial_periods(cnpj,doc_type,year,date_ref);
        CREATE TABLE IF NOT EXISTS cvm_imports(
          doc_type TEXT, year INTEGER, imported_at TEXT, source_url TEXT, row_count INTEGER,
          status TEXT, message TEXT, PRIMARY KEY(doc_type,year)
        );
        CREATE TABLE IF NOT EXISTS fundamentals(
          cnpj TEXT PRIMARY KEY, reference_date TEXT, origin TEXT,
          revenue_ltm REAL, profit_ltm REAL, ebitda_ltm REAL, equity REAL, cash REAL,
          debt REAL, net_debt REAL, roe_avg_5y REAL, profit_cagr_5y REAL,
          net_margin_latest REAL, margin_trend TEXT, positive_profits_5y INTEGER,
          net_debt_ebitda REAL, payout REAL, is_financial INTEGER,
          quality_status TEXT, quality_score REAL, failures INTEGER, pending INTEGER,
          reason TEXT, criteria_json TEXT, source_url TEXT, updated_at TEXT
        );
        """
    )
    conn.commit()


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4, connect=4, read=4, backoff_factor=1,
        status_forcelist=(429,500,502,503,504),
        allowed_methods=frozenset({"GET"}),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "AnalisadorB3Educacional/2.0"
    return s


def package_url(doc_type: str, year: int) -> str:
    return f"{CVM_BASE}/{doc_type}/DADOS/{doc_type.lower()}_cia_aberta_{year}.zip"


def archive_members(archive: zipfile.ZipFile, doc_type: str, year: int) -> list[tuple[str,str,int]]:
    names = {Path(name).name.lower(): name for name in archive.namelist()}
    found: list[tuple[str,str,int]] = []
    for statement in ("DRE", "DFC_MI", "BPA", "BPP"):
        for suffix, priority in (("con",2),("ind",1)):
            wanted = f"{doc_type.lower()}_cia_aberta_{statement.lower()}_{suffix}_{year}.csv"
            if wanted in names:
                found.append((statement,names[wanted],priority))
    return found


def parse_package(content: bytes, doc_type: str, year: int, active: set[str]) -> list[dict]:
    chosen: dict[tuple[str,str,str], tuple[int,int,str,float,str]] = {}
    starts: dict[tuple[str,str], str] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive_members(archive,doc_type,year)
        if not members:
            raise RuntimeError(f"Pacote {doc_type} {year} sem DRE/DFC/BPA/BPP")
        for statement, member, priority in members:
            with archive.open(member) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw,encoding="latin-1",newline=""),delimiter=";")
                if not reader.fieldnames:
                    continue
                reader.fieldnames = [str(x or "").lstrip("\ufeff").strip() for x in reader.fieldnames]
                for row in reader:
                    cnpj = norm_cnpj(row.get("CNPJ_CIA"))
                    if cnpj not in active or not norm_text(row.get("ORDEM_EXERC")).startswith("ULTIMO"):
                        continue
                    ref = iso_date(row.get("DT_REFER"))
                    if not ref:
                        continue
                    start = iso_date(row.get("DT_INI_EXERC"))
                    try:
                        version = int(float(str(row.get("VERSAO") or "0").replace(",",".")))
                    except ValueError:
                        version = 0
                    account = str(row.get("CD_CONTA") or "").strip()
                    desc = norm_text(row.get("DS_CONTA"))
                    value = number(row.get("VL_CONTA"),row.get("ESCALA_MOEDA"))
                    if value is None:
                        continue
                    field = None
                    if statement == "DRE":
                        field = {"3.01":"revenue","3.05":"ebit","3.11":"profit"}.get(account)
                    elif statement == "BPA" and account == "1.01.01":
                        field = "cash"
                    elif statement == "BPP" and account == "2.03":
                        field = "equity"
                    elif statement == "BPP" and account in {"2.01.04","2.02.01"}:
                        field = "debt_current" if account == "2.01.04" else "debt_long"
                    elif statement == "DFC_MI" and ("DEPRECIAC" in desc or "AMORTIZAC" in desc):
                        field = "da"
                    if not field:
                        continue
                    key = (cnpj,ref,field)
                    candidate = (priority,version,start,value,member)
                    current = chosen.get(key)
                    if current is None or candidate[:2] > current[:2] or (
                        candidate[:2] == current[:2] and abs(value) > abs(current[3])
                    ):
                        chosen[key] = candidate
                    if start:
                        starts[(cnpj,ref)] = start
    periods: dict[tuple[str,str],dict] = {}
    for (cnpj,ref,field),(_,_,_,value,member) in chosen.items():
        item = periods.setdefault((cnpj,ref),{
            "doc_type":doc_type,"year":year,"cnpj":cnpj,"date_ref":ref,
            "date_start":starts.get((cnpj,ref),""),"revenue":None,"ebit":None,
            "profit":None,"da":None,"cash":None,"equity":None,"debt":None,
            "source_file":member,
        })
        if field == "debt_current":
            item["debt"] = (item["debt"] or 0) + value
        elif field == "debt_long":
            item["debt"] = (item["debt"] or 0) + value
        elif field == "da":
            item["da"] = (item["da"] or 0) + abs(value)
        else:
            item[field] = value
    return list(periods.values())


def import_year(conn: sqlite3.Connection, doc_type: str, year: int, active: set[str]) -> int:
    url = package_url(doc_type,year)
    now = datetime.now(timezone.utc).isoformat()
    response = session().get(url,timeout=180)
    if response.status_code == 404:
        conn.execute(
            "INSERT OR REPLACE INTO cvm_imports VALUES(?,?,?,?,?,?,?)",
            (doc_type,year,now,url,0,"AUSENTE","Pacote ainda não publicado"),
        )
        conn.commit()
        return 0
    response.raise_for_status()
    rows = parse_package(response.content,doc_type,year,active)
    conn.execute("DELETE FROM financial_periods WHERE doc_type=? AND year=?",(doc_type,year))
    conn.executemany(
        """INSERT INTO financial_periods(
        doc_type,year,cnpj,date_ref,date_start,revenue,ebit,profit,da,cash,equity,debt,source_file,imported_at
        ) VALUES(:doc_type,:year,:cnpj,:date_ref,:date_start,:revenue,:ebit,:profit,:da,:cash,:equity,:debt,:source_file,:imported_at)""",
        [{**row,"imported_at":now} for row in rows],
    )
    conn.execute(
        "INSERT OR REPLACE INTO cvm_imports VALUES(?,?,?,?,?,?,?)",
        (doc_type,year,now,url,len(rows),"OK","Importação concluída"),
    )
    conn.commit()
    return len(rows)


def period_rows(conn: sqlite3.Connection, cnpj: str, doc_type: str, year: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM financial_periods WHERE cnpj=? AND doc_type=? AND year=? ORDER BY date_ref",
        (cnpj,doc_type,year),
    ))


def latest(rows: list[sqlite3.Row]) -> sqlite3.Row | None:
    return max(rows,key=lambda row: row["date_ref"]) if rows else None


def same_period(rows: list[sqlite3.Row], current_ref: str) -> sqlite3.Row | None:
    suffix = current_ref[5:]
    options = [row for row in rows if str(row["date_ref"])[5:] == suffix]
    return latest(options)


def ltm(current: float | None, annual: float | None, comparative: float | None) -> float | None:
    if current is None or annual is None or comparative is None:
        return annual
    return current + annual - comparative


def slope(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    xs = list(range(len(values)))
    xbar, ybar = mean(xs), mean(values)
    den = sum((x-xbar)**2 for x in xs)
    return sum((x-xbar)*(y-ybar) for x,y in zip(xs,values))/den if den else None


def criterion(
    name: str,
    value: str,
    limit: str,
    status: str,
    note: str = "",
    *,
    essential: bool = True,
) -> dict:
    return {
        "name": name,
        "value": value,
        "limit": limit,
        "status": status,
        "note": note,
        "essential": essential,
    }


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value*100:.2f}%".replace(".",",")


def mult(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}x".replace(".",",")


def calculate_company(
    annual: dict[int,sqlite3.Row | None], annual_end: int, revenue: float | None,
    profit: float | None, ebit: float | None, da: float | None,
    reference_date: str, origin: str, balance: sqlite3.Row | None, financial: bool,
) -> dict:
    years = list(range(annual_end-4,annual_end+1))
    profits = {year:(annual[year]["profit"] if annual.get(year) else None) for year in years}
    revenues = {year:(annual[year]["revenue"] if annual.get(year) else None) for year in years}
    equities = {year:(annual[year]["equity"] if annual.get(year) else None) for year in years}
    roes = [profits[y]/equities[y] for y in years if profits[y] is not None and equities[y] not in (None,0)]
    roe = mean(roes) if roes else None
    first,last = profits.get(years[0]),profits.get(years[-1])
    cagr = (last/first)**(1/4)-1 if first and last and first>0 and last>0 else None
    margins = [profits[y]/revenues[y] for y in years if profits[y] is not None and revenues[y] not in (None,0)]
    margin_latest = profit/revenue if profit is not None and revenue not in (None,0) else None
    trend_slope = slope(margins)
    margin_trend = "PENDENTE" if trend_slope is None else ("ESTÁVEL/CRESCENTE" if trend_slope >= -0.005 else "DETERIORAÇÃO")
    positive = sum(value is not None and value>0 for value in profits.values())
    ebitda = ebit + abs(da) if ebit is not None and da is not None else None
    equity = balance["equity"] if balance else None
    cash = balance["cash"] if balance else None
    debt = balance["debt"] if balance else None
    net_debt = debt-cash if debt is not None and cash is not None else None
    debt_ratio = net_debt/ebitda if net_debt is not None and ebitda and ebitda>0 else None

    criteria: list[dict] = []

    def add(name, value, limit, status, note="", *, essential=True):
        criteria.append(
            criterion(
                name,
                value,
                limit,
                status,
                note,
                essential=essential,
            )
        )

    add("ROE médio 5A",pct(roe),"> 12,00%","PENDENTE" if roe is None else ("APROVADO" if roe>0.12 else "REPROVADO"))
    add("Crescimento do lucro 5A",pct(cagr),"> 0,00%","PENDENTE" if cagr is None else ("APROVADO" if cagr>0 else "REPROVADO"))
    if financial:
        add(
            "Dívida líquida/EBITDA",
            "N/A SETOR",
            "Não aplicável",
            "INFORMATIVO",
            "Métrica industrial inadequada para instituições financeiras.",
            essential=False,
        )
    else:
        add("Dívida líquida/EBITDA",mult(debt_ratio),"< 3,00x","PENDENTE" if debt_ratio is None else ("APROVADO" if debt_ratio<3 else "REPROVADO"))
    if financial:
        add(
            "Margem líquida",
            pct(margin_latest),
            "Contexto setorial",
            "INFORMATIVO",
            "Não integra o filtro financeiro; eficiência e risco de crédito são usados no lugar.",
            essential=False,
        )
    else:
        add("Margem líquida",pct(margin_latest),"Estável ou crescente","PENDENTE" if margin_trend=="PENDENTE" else ("APROVADO" if margin_trend=="ESTÁVEL/CRESCENTE" else "REPROVADO"),margin_trend)
    add("Lucros positivos 5A",f"{positive}/5","≥ 4/5","APROVADO" if positive>=4 else "REPROVADO")
    add("Payout","Pendente","< 90,00%","PENDENTE","Proventos ainda não conciliados; valuation bloqueado.")

    if financial:
        add(
            "Dados regulatórios essenciais",
            "Pendente",
            "Basileia, eficiência, inadimplência e cobertura conciliados",
            "PENDENTE",
            "O IFData/SUSEP precisa ser integrado antes da decisão do filtro.",
        )

    recurring_losses = positive <= 2 or sum((profits[y] or 0)<=0 for y in years[-3:] if profits[y] is not None)>=2
    debt_series=[]
    for y in years[-3:]:
        row=annual.get(y)
        if row and row["debt"] is not None and row["cash"] is not None:
            debt_series.append(row["debt"]-row["cash"])
    explosive = len(debt_series)>=3 and debt_series[-1]>0 and debt_series[0]>0 and debt_series[-1]>debt_series[0]*1.8
    structural_red = recurring_losses or explosive
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
    return {
        "reference_date":reference_date,"origin":origin,"revenue_ltm":revenue,
        "profit_ltm":profit,"ebitda_ltm":ebitda,"equity":equity,"cash":cash,
        "debt":debt,"net_debt":net_debt,"roe_avg_5y":roe,"profit_cagr_5y":cagr,
        "net_margin_latest":margin_latest,"margin_trend":margin_trend,
        "positive_profits_5y":positive,"net_debt_ebitda":debt_ratio,"payout":None,
        "quality_status":result.status,"quality_score":score,"failures":result.rejected,
        "pending":result.pending,"reason":reason,
        "criteria_json":json.dumps(criteria,ensure_ascii=False,separators=(",",":")),
    }


def calculate_fundamentals(conn: sqlite3.Connection, issuer_metadata: dict[str,dict]) -> int:
    current_year=date.today().year
    annual_end=current_year-1
    now=datetime.now(timezone.utc).isoformat()
    count=0
    for cnpj,meta in issuer_metadata.items():
        annual={year:latest(period_rows(conn,cnpj,"DFP",year)) for year in range(annual_end-5,annual_end+1)}
        annual_latest=annual.get(annual_end)
        current_itr=period_rows(conn,cnpj,"ITR",current_year)
        prior_itr=period_rows(conn,cnpj,"ITR",annual_end)
        latest_itr=latest(current_itr)
        if annual_latest:
            revenue,profit,ebit,da=(annual_latest[x] for x in ("revenue","profit","ebit","da"))
            reference_date=annual_latest["date_ref"]
            origin=f"CVM DFP {annual_end}"
        else:
            revenue=profit=ebit=da=None
            reference_date=""
            origin="DFP anual não localizado"
        if latest_itr and annual_latest:
            comp=same_period(prior_itr,latest_itr["date_ref"])
            if comp:
                revenue=ltm(latest_itr["revenue"],annual_latest["revenue"],comp["revenue"])
                profit=ltm(latest_itr["profit"],annual_latest["profit"],comp["profit"])
                ebit=ltm(latest_itr["ebit"],annual_latest["ebit"],comp["ebit"])
                da=ltm(latest_itr["da"],annual_latest["da"],comp["da"])
                reference_date=latest_itr["date_ref"]
                origin=f"CVM ITR {current_year} + DFP {annual_end} − ITR comparável {annual_end}"
        balance=latest(current_itr) or annual_latest
        financial=is_financial_company(meta)
        if not annual_latest:
            result={
                "reference_date":reference_date,"origin":origin,"revenue_ltm":revenue,
                "profit_ltm":profit,"ebitda_ltm":None,"equity":balance["equity"] if balance else None,
                "cash":balance["cash"] if balance else None,"debt":balance["debt"] if balance else None,
                "net_debt":None,"roe_avg_5y":None,"profit_cagr_5y":None,
                "net_margin_latest":None,"margin_trend":"PENDENTE","positive_profits_5y":0,
                "net_debt_ebitda":None,"payout":None,"quality_status":"PENDENTE FUNDAMENTOS",
                "quality_score":None,"failures":0,"pending":6,
                "reason":"DFP anual não localizado para o emissor",
                "criteria_json":json.dumps([criterion("Fundamentos CVM","Não localizados","DFP/ITR disponíveis","PENDENTE")],ensure_ascii=False),
            }
        else:
            result=calculate_company(annual,annual_end,revenue,profit,ebit,da,reference_date,origin,balance,financial)
        conn.execute(
            """INSERT INTO fundamentals(
            cnpj,reference_date,origin,revenue_ltm,profit_ltm,ebitda_ltm,equity,cash,debt,net_debt,
            roe_avg_5y,profit_cagr_5y,net_margin_latest,margin_trend,positive_profits_5y,
            net_debt_ebitda,payout,is_financial,quality_status,quality_score,failures,pending,
            reason,criteria_json,source_url,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(cnpj) DO UPDATE SET
            reference_date=excluded.reference_date,origin=excluded.origin,revenue_ltm=excluded.revenue_ltm,
            profit_ltm=excluded.profit_ltm,ebitda_ltm=excluded.ebitda_ltm,equity=excluded.equity,
            cash=excluded.cash,debt=excluded.debt,net_debt=excluded.net_debt,
            roe_avg_5y=excluded.roe_avg_5y,profit_cagr_5y=excluded.profit_cagr_5y,
            net_margin_latest=excluded.net_margin_latest,margin_trend=excluded.margin_trend,
            positive_profits_5y=excluded.positive_profits_5y,net_debt_ebitda=excluded.net_debt_ebitda,
            payout=excluded.payout,is_financial=excluded.is_financial,quality_status=excluded.quality_status,
            quality_score=excluded.quality_score,failures=excluded.failures,pending=excluded.pending,
            reason=excluded.reason,criteria_json=excluded.criteria_json,source_url=excluded.source_url,
            updated_at=excluded.updated_at""",
            (cnpj,result["reference_date"],result["origin"],result["revenue_ltm"],result["profit_ltm"],
             result["ebitda_ltm"],result["equity"],result["cash"],result["debt"],result["net_debt"],
             result["roe_avg_5y"],result["profit_cagr_5y"],result["net_margin_latest"],result["margin_trend"],
             result["positive_profits_5y"],result["net_debt_ebitda"],None,1 if financial else 0,
             result["quality_status"],result["quality_score"],result["failures"],result["pending"],
             result["reason"],result["criteria_json"],FUNDAMENTALS_SOURCE,now),
        )
        count+=1
    conn.commit()
    return count


def load_and_score_fundamentals(conn: sqlite3.Connection, issuers: list[dict]) -> dict:
    ensure_fundamentals_schema(conn)
    metadata={norm_cnpj(row.get("cnpj")):row for row in issuers if norm_cnpj(row.get("cnpj"))}
    active=set(metadata)
    current=date.today().year
    imported=0
    for year in range(current-6,current):
        imported+=import_year(conn,"DFP",year,active)
    for year in (current-1,current):
        imported+=import_year(conn,"ITR",year,active)
    processed=calculate_fundamentals(conn,metadata)
    red=int(conn.execute("SELECT COUNT(*) FROM fundamentals WHERE quality_status='ALERTA VERMELHO'").fetchone()[0])
    return {"imported_periods":imported,"processed":processed,"red_alerts":red}
