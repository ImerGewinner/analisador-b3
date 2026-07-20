from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timezone
from difflib import SequenceMatcher

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import app

BASE = "https://olinda.bcb.gov.br/olinda/servico/IFDATA/versao/v1/odata"
SOURCE = "Banco Central do Brasil — IFData (Cosif/SCR), conglomerados prudenciais"
REPORTS = (1, 4, 5, 8, 16)
ALIASES = {
    "ABCB": "ABC BRASIL", "BBAS": "BANCO DO BRASIL", "BBDC": "BRADESCO",
    "BEES": "BANESTES", "BGIP": "BANCO DO ESTADO DE SERGIPE", "BMEB": "MERCANTIL DO BRASIL",
    "BNBR": "BANCO DO NORDESTE", "BPAC": "BTG PACTUAL", "BRSR": "BANRISUL",
    "ITUB": "ITAU UNIBANCO", "PINE": "PINE", "SANB": "SANTANDER", "BAZA": "BANCO DA AMAZONIA",
}


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).upper()
    text = re.sub(r"\b(S A|SA|S\.A\.|BANCO|BCO|HOLDING|HOLDINGS|PARTICIPACOES|PARTICIPACAO|BRASIL)\b", " ", text)
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, connect=4, read=4, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET"}))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "AnalisadorB3Educacional/4.0"
    return s


def csv_rows(response: requests.Response) -> list[dict]:
    response.raise_for_status()
    text = response.content.decode("utf-8-sig", errors="replace")
    if not text.strip():
        return []
    sample = text[:4096]
    delimiter = ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;").delimiter
    except csv.Error:
        pass
    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


def quarter_candidates() -> list[int]:
    today = date.today()
    values: list[int] = []
    for year in range(today.year, today.year - 3, -1):
        for month in (12, 9, 6, 3):
            if year == today.year and month > today.month:
                continue
            values.append(year * 100 + month)
    return values


def fetch_registry(s: requests.Session, period: int) -> list[dict]:
    url = f"{BASE}/IfDataCadastro(AnoMes=@AnoMes)"
    params = {
        "@AnoMes": period, "$format": "text/csv",
        "$select": "CodInst,Data,NomeInstituicao,Tcb,Td,Tc,SegmentoTb,Atividade,Uf,Municipio,Sr,CodConglomeradoFinanceiro,CodConglomeradoPrudencial,CnpjInstituicaoLider,Situacao",
    }
    return csv_rows(s.get(url, params=params, timeout=120))


def latest_period(s: requests.Session) -> tuple[int, list[dict]]:
    for period in quarter_candidates():
        try:
            rows = fetch_registry(s, period)
            if rows:
                return period, rows
        except Exception as exc:
            print(f"IFData cadastro {period}: {exc}")
    raise RuntimeError("IFData sem cadastro disponível nos últimos trimestres")


def fetch_report(s: requests.Session, period: int, report: int) -> list[dict]:
    url = f"{BASE}/IfDataValores(AnoMes=@AnoMes,TipoInstituicao=@TipoInstituicao,Relatorio=@Relatorio)"
    params = {
        "@AnoMes": period, "@TipoInstituicao": 1, "@Relatorio": f"'{report}'", "$format": "text/csv",
        "$select": "TipoInstituicao,CodInst,AnoMes,NomeRelatorio,NumeroRelatorio,Grupo,Conta,NomeColuna,DescricaoColuna,Saldo",
    }
    return csv_rows(s.get(url, params=params, timeout=240))


def parse_float(value: object) -> float | None:
    raw = str(value or "").strip().replace(" ", "")
    if not raw:
        return None
    try:
        if "," in raw and "." in raw:
            raw = raw.replace(",", "")
        elif "," in raw:
            raw = raw.replace(",", ".")
        return float(raw)
    except ValueError:
        return None


def metric_score(text: str, metric: str) -> int:
    t = norm(text)
    rules = {
        "basel": (("INDICE BASILEIA", 100), ("BASILEIA", 80)),
        "capital": (("INDICE CAPITAL PRINCIPAL", 100), ("CAPITAL PRINCIPAL", 80)),
        "tier1": (("INDICE NIVEL I", 100), ("NIVEL I", 70)),
        "efficiency": (("INDICE EFICIENCIA", 100), ("EFICIENCIA OPERACIONAL", 95), ("EFICIENCIA", 60)),
        "npl": (("INADIMPLENCIA ACIMA 90", 100), ("INADIMPLENCIA", 75), ("ATRASO ACIMA 90", 70)),
        "coverage": (("INDICE COBERTURA", 100), ("COBERTURA INADIMPLENCIA", 95), ("COBERTURA", 50)),
        "provisions": (("PROVISAO PARA PERDAS", 90), ("PROVISAO OPERACOES CREDITO", 85), ("PROVISAO", 40)),
    }
    best = 0
    for phrase, score in rules[metric]:
        if phrase in t:
            best = max(best, score)
    return best


def extract_metrics(rows: list[dict]) -> dict[str, dict[str, float]]:
    selected: dict[str, dict[str, tuple[int, float, str]]] = {}
    for row in rows:
        code = str(row.get("CodInst") or "").strip()
        value = parse_float(row.get("Saldo"))
        if not code or value is None:
            continue
        label = f"{row.get('NomeColuna','')} {row.get('DescricaoColuna','')} {row.get('Grupo','')}"
        for metric in ("basel", "capital", "tier1", "efficiency", "npl", "coverage", "provisions"):
            score = metric_score(label, metric)
            if not score:
                continue
            current = selected.setdefault(code, {}).get(metric)
            if current is None or score > current[0]:
                selected[code][metric] = (score, value, label)
    return {code: {metric: value[1] for metric, value in metrics.items()} for code, metrics in selected.items()}


def candidate_score(company: sqlite3.Row, registry: dict) -> float:
    root = str(company["ticker"])[:4]
    target = norm(ALIASES.get(root) or company["trading_name"] or company["company_name"])
    name = norm(registry.get("NomeInstituicao"))
    if not target or not name:
        return 0.0
    similarity = SequenceMatcher(None, target, name).ratio()
    target_tokens, name_tokens = set(target.split()), set(name.split())
    overlap = len(target_tokens & name_tokens) / max(1, len(target_tokens))
    cnpj8 = re.sub(r"\D", "", str(company["cnpj"] or ""))[:8]
    leader = re.sub(r"\D", "", str(registry.get("CnpjInstituicaoLider") or ""))[:8]
    cnpj_bonus = 1.0 if cnpj8 and leader and cnpj8 == leader else 0.0
    return similarity * 0.55 + overlap * 0.35 + cnpj_bonus


def normalize_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 100.0 if abs(value) > 1.5 else value


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS financial_regulatory(
        ticker TEXT PRIMARY KEY,period INTEGER,cod_inst TEXT,institution_name TEXT,match_score REAL,
        basel REAL,capital_principal REAL,tier1 REAL,efficiency REAL,npl REAL,coverage REAL,provisions REAL,
        status TEXT,source_url TEXT,updated_at TEXT)"""
    )
    conn.commit()


def fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value*100:.2f}%".replace(".", ",")


def run() -> int:
    conn = app.connect()
    ensure_schema(conn)
    s = session()
    try:
        period, registry = latest_period(s)
        report_rows: list[dict] = []
        for report in REPORTS:
            try:
                rows = fetch_report(s, period, report)
                report_rows.extend(rows)
                print(f"IFData {period} relatório {report}: {len(rows)} linhas")
            except Exception as exc:
                print(f"Aviso IFData relatório {report}: {exc}")
        metrics_by_code = extract_metrics(report_rows)
        companies = conn.execute(
            """SELECT ticker,cnpj,company_name,trading_name,segment FROM universe
               WHERE principal='SIM' AND UPPER(COALESCE(segment,'')) REGEXP 'BANCO|FINANCEIR|SEGURO|SEGURADORA|PREVIDENCIA|CREDITO|INTERMEDI'"""
        ).fetchall()
        # SQLite não possui REGEXP por padrão; refaz a seleção de forma portável.
        if not companies:
            companies = conn.execute("SELECT ticker,cnpj,company_name,trading_name,segment FROM universe WHERE principal='SIM'").fetchall()
            companies = [row for row in companies if re.search(r"BANCO|FINANCEIR|SEGURO|SEGURADORA|PREVID|CREDITO|INTERMEDI", norm(row["segment"]))]
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("DELETE FROM financial_regulatory")
        mapped = 0
        for company in companies:
            ranked = sorted(((candidate_score(company, item), item) for item in registry), key=lambda pair: pair[0], reverse=True)
            score, match = ranked[0] if ranked else (0.0, {})
            code = str(match.get("CodInst") or "")
            values = metrics_by_code.get(code, {}) if score >= 0.62 else {}
            basel = normalize_ratio(values.get("basel")); capital = normalize_ratio(values.get("capital")); tier1 = normalize_ratio(values.get("tier1"))
            efficiency = normalize_ratio(values.get("efficiency")); npl = normalize_ratio(values.get("npl")); coverage = normalize_ratio(values.get("coverage"))
            status = "VALIDADO IFData" if values else ("INSTITUIÇÃO MAPEADA — MÉTRICAS NÃO LOCALIZADAS" if score >= 0.62 else "PENDENTE MAPEAMENTO IFData")
            if values:
                mapped += 1
            conn.execute(
                "INSERT INTO financial_regulatory VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (company["ticker"], period, code, match.get("NomeInstituicao", ""), score, basel, capital, tier1, efficiency, npl, coverage, values.get("provisions"), status, BASE, now),
            )
        conn.commit()
        path = app.DOCS_DIR / "data.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        regulatory = {row["ticker"]: row for row in conn.execute("SELECT * FROM financial_regulatory")}
        for item in payload.get("items", []):
            row = regulatory.get(item.get("ticker"))
            if not row:
                continue
            item.update({
                "ifdataPeriodo": str(row["period"]), "ifdataInstituicao": row["institution_name"], "ifdataStatus": row["status"],
                "basileia": fmt_pct(row["basel"]), "basileiaRaw": row["basel"], "capitalPrincipal": fmt_pct(row["capital_principal"]),
                "nivel1": fmt_pct(row["tier1"]), "eficiencia": fmt_pct(row["efficiency"]), "inadimplencia": fmt_pct(row["npl"]),
                "cobertura": fmt_pct(row["coverage"]), "fonteRegulatoria": SOURCE,
            })
        payload["ifdata"] = {"period": period, "mapped": mapped, "financialCompanies": len(companies), "source": SOURCE, "updatedAt": now}
        payload.setdefault("methodology", {})["financial"] = "Bancos: ROE, crescimento, payout e métricas regulatórias IFData (Basileia, capital, eficiência, inadimplência e cobertura quando disponíveis). Seguradoras permanecem com ressalva até integração SUSEP."
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        status_path = app.DOCS_DIR / "status.json"
        status_payload = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
        status_payload.update({"ifdataPeriod": period, "ifdataMapped": mapped, "ifdataFinancialCompanies": len(companies), "ifdataUpdatedAt": now})
        status_path.write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        app.log(conn, "OK", f"IFData {period}: {mapped}/{len(companies)} companhias financeiras com métricas regulatórias mapeadas.")
        print(json.dumps({"status": "OK", "period": period, "mapped": mapped, "companies": len(companies)}, ensure_ascii=False))
        return 0
    except Exception as exc:
        app.log(conn, "ERRO", f"IFData: {exc!r}")
        print(f"ERRO IFData: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
