from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import unicodedata
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import app

CVM_BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC"
SOURCE = "CVM DFP/ITR — DRE, lucro básico por ação"


def norm_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().upper()


def norm_cnpj(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def iso_date(value: object) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", text):
        return f"{text[6:10]}-{text[3:5]}-{text[0:2]}"
    return text[:10]


def decimal_number(value: object) -> float | None:
    raw = str(value or "").strip().replace(" ", "")
    if not raw:
        return None
    try:
        if "," in raw and "." in raw:
            normalized = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
        elif "," in raw:
            normalized = raw.replace(",", ".")
        else:
            normalized = raw
        return float(normalized)
    except ValueError:
        return None


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "AnalisadorB3Educacional/3.1"
    return s


def package_url(doc_type: str, year: int) -> str:
    return f"{CVM_BASE}/{doc_type}/DADOS/{doc_type.lower()}_cia_aberta_{year}.zip"


def eps_field(account: str, description: str) -> str | None:
    desc = norm_text(description)
    if account == "3.99.01.01":
        return "eps_on"
    if account == "3.99.01.02":
        return "eps_pn"
    if account.startswith("3.99.01"):
        if re.search(r"\bON\b", desc) or "ORDIN" in desc:
            return "eps_on"
        if re.search(r"\bPN[A-F]?\b", desc) or "PREFER" in desc:
            return "eps_pn"
    return None


def parse_package(content: bytes, doc_type: str, year: int, active: set[str]) -> list[dict]:
    chosen: dict[tuple[str, str, str], tuple[int, int, float, str]] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = {Path(name).name.lower(): name for name in archive.namelist()}
        members: list[tuple[str, int]] = []
        for suffix, priority in (("con", 2), ("ind", 1)):
            wanted = f"{doc_type.lower()}_cia_aberta_dre_{suffix}_{year}.csv"
            if wanted in names:
                members.append((names[wanted], priority))
        if not members:
            raise RuntimeError(f"Pacote {doc_type} {year} sem arquivo DRE")

        for member, priority in members:
            with archive.open(member) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding="latin-1", newline=""), delimiter=";")
                if not reader.fieldnames:
                    continue
                reader.fieldnames = [str(name or "").lstrip("\ufeff").strip() for name in reader.fieldnames]
                for row in reader:
                    cnpj = norm_cnpj(row.get("CNPJ_CIA"))
                    if cnpj not in active or not norm_text(row.get("ORDEM_EXERC")).startswith("ULTIMO"):
                        continue
                    reference = iso_date(row.get("DT_REFER"))
                    if not reference:
                        continue
                    field = eps_field(str(row.get("CD_CONTA") or "").strip(), str(row.get("DS_CONTA") or ""))
                    if not field:
                        continue
                    value = decimal_number(row.get("VL_CONTA"))
                    if value is None:
                        continue
                    try:
                        version = int(float(str(row.get("VERSAO") or "0").replace(",", ".")))
                    except ValueError:
                        version = 0
                    key = (cnpj, reference, field)
                    candidate = (priority, version, value, member)
                    current = chosen.get(key)
                    if current is None or candidate[:2] > current[:2]:
                        chosen[key] = candidate

    periods: dict[tuple[str, str], dict] = {}
    for (cnpj, reference, field), (_, _, value, member) in chosen.items():
        item = periods.setdefault(
            (cnpj, reference),
            {
                "doc_type": doc_type,
                "year": year,
                "cnpj": cnpj,
                "reference_date": reference,
                "eps_on": None,
                "eps_pn": None,
                "source_file": member,
            },
        )
        item[field] = value
    return list(periods.values())


def latest(rows: list[dict]) -> dict | None:
    return max(rows, key=lambda row: row["reference_date"]) if rows else None


def same_period(rows: list[dict], reference: str) -> dict | None:
    suffix = reference[5:]
    matches = [row for row in rows if row["reference_date"][5:] == suffix]
    return latest(matches)


def ltm(current: float | None, annual: float | None, comparative: float | None) -> float | None:
    if current is None or annual is None or comparative is None:
        return annual
    return current + annual - comparative


def load_package(doc_type: str, year: int, active: set[str]) -> list[dict]:
    url = package_url(doc_type, year)
    response = session().get(url, timeout=180)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    return parse_package(response.content, doc_type, year, active)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS eps_metrics(
          cnpj TEXT PRIMARY KEY,
          reference_date TEXT,
          origin TEXT,
          eps_on_ltm REAL,
          eps_pn_ltm REAL,
          source_url TEXT,
          updated_at TEXT
        );
        """
    )
    conn.commit()


def run() -> int:
    conn = app.connect()
    ensure_schema(conn)
    try:
        active = {row[0] for row in conn.execute("SELECT DISTINCT cnpj FROM universe WHERE cnpj<>''")}
        current_year = date.today().year
        annual_year = current_year - 1

        annual_rows = load_package("DFP", annual_year, active)
        current_itr_rows = load_package("ITR", current_year, active)
        prior_itr_rows = load_package("ITR", annual_year, active)

        by_annual: dict[str, list[dict]] = {}
        by_current: dict[str, list[dict]] = {}
        by_prior: dict[str, list[dict]] = {}
        for row in annual_rows:
            by_annual.setdefault(row["cnpj"], []).append(row)
        for row in current_itr_rows:
            by_current.setdefault(row["cnpj"], []).append(row)
        for row in prior_itr_rows:
            by_prior.setdefault(row["cnpj"], []).append(row)

        conn.execute("DELETE FROM eps_metrics")
        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        for cnpj in active:
            annual = latest(by_annual.get(cnpj, []))
            current = latest(by_current.get(cnpj, []))
            comparative = same_period(by_prior.get(cnpj, []), current["reference_date"]) if current else None

            eps_on = annual.get("eps_on") if annual else None
            eps_pn = annual.get("eps_pn") if annual else None
            reference = annual["reference_date"] if annual else ""
            origin = f"CVM DFP {annual_year} — lucro básico por ação" if annual else "LPA CVM não localizado"

            if current and annual and comparative:
                eps_on = ltm(current.get("eps_on"), annual.get("eps_on"), comparative.get("eps_on"))
                eps_pn = ltm(current.get("eps_pn"), annual.get("eps_pn"), comparative.get("eps_pn"))
                reference = current["reference_date"]
                origin = f"CVM ITR {current_year} + DFP {annual_year} − ITR comparável {annual_year}"

            if eps_on is None and eps_pn is None:
                continue
            conn.execute(
                "INSERT INTO eps_metrics VALUES(?,?,?,?,?,?,?)",
                (cnpj, reference, origin, eps_on, eps_pn, SOURCE, now),
            )
            inserted += 1

        conn.commit()
        print(
            json.dumps(
                {
                    "status": "OK",
                    "emissores_com_lpa": inserted,
                    "dfp": len(annual_rows),
                    "itr_atual": len(current_itr_rows),
                    "itr_comparavel": len(prior_itr_rows),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        print(f"ERRO LPA CVM: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
