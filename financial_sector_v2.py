from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher

import app
import financial_sector as base
import quality_rules


BASEL_MIN = 0.105
EFFICIENCY_MAX = 0.60
NPL_MAX = 0.05
COVERAGE_MIN = 1.00
REGULATORY_CRITERIA = {
    "DADOS REGULATORIOS ESSENCIAIS",
    "INDICE DE BASILEIA",
    "EFICIENCIA OPERACIONAL",
    "INADIMPLENCIA ACIMA DE 90 DIAS",
    "COBERTURA DA INADIMPLENCIA",
    "SOLVENCIA E CAPITAL SUSEP",
    "CAPITAL PRINCIPAL",
    "NIVEL I",
    "PROVISOES",
}


def digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def root_for(ticker: object) -> str:
    return str(ticker or "")[:4].upper()


def match_norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).upper()
    text = re.sub(
        r"\b(S A|SA|S\.A\.|HOLDING|HOLDINGS|PARTICIPACOES|PARTICIPACAO|CIA|COMPANHIA)\b",
        " ",
        text,
    )
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def code_key(value: object) -> str:
    raw = str(value or "").strip()
    numeric = digits(raw)
    return numeric.lstrip("0") or ("0" if numeric else raw.upper())


def is_bank(company) -> bool:
    segment = base.norm(company["segment"])
    return (
        root_for(company["ticker"]) in base.ALIASES
        or bool(
            re.search(
                r"BANCO|BANCOS|INTERMEDIARIOS FINANCEIROS|CREDITO E FINANCIAMENTO",
                segment,
            )
        )
    )


def is_insurer(company) -> bool:
    segment = base.norm(company["segment"])
    return (
        bool(re.search(r"\bSEGURADORAS?\b|\bRESSEGURADORAS?\b|\bPREVIDENCIA\b", segment))
        and "CORRETORAS DE SEGUROS" not in segment
        and not is_bank(company)
    )


def registry_score(company, registry: dict) -> float:
    root = root_for(company["ticker"])
    target = match_norm(
        base.ALIASES.get(root)
        or company["trading_name"]
        or company["company_name"]
    )
    name = match_norm(registry.get("NomeInstituicao"))
    if not target or not name:
        score = 0.0
    else:
        similarity = SequenceMatcher(None, target, name).ratio()
        target_tokens = set(target.split())
        name_tokens = set(name.split())
        overlap = len(target_tokens & name_tokens) / max(1, len(target_tokens))
        score = similarity * 0.55 + overlap * 0.45

    company_cnpj = digits(company["cnpj"])
    leader_cnpj = digits(registry.get("CnpjInstituicaoLider"))
    if company_cnpj and leader_cnpj:
        if company_cnpj == leader_cnpj:
            score += 3.0
        elif company_cnpj[:8] == leader_cnpj[:8]:
            score += 2.0
    return score


def normalized_metrics(metrics_by_code: dict[str, dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for raw_code, metrics in metrics_by_code.items():
        result[code_key(raw_code)] = metrics
    return result


def plausible_ratio(metric: str, value: float | None) -> float | None:
    if value is None:
        return None
    limits = {
        "basel": (0.0, 1.0),
        "capital": (0.0, 1.0),
        "tier1": (0.0, 1.0),
        "efficiency": (0.0, 2.0),
        "npl": (0.0, 1.0),
        "coverage": (0.0, 20.0),
    }
    low, high = limits[metric]
    return value if low <= value <= high else None


def report_code(registry: dict, metrics_by_code: dict[str, dict]) -> str:
    candidates = [
        registry.get("CodConglomeradoPrudencial"),
        registry.get("CodConglomeradoFinanceiro"),
        registry.get("CodInst"),
    ]
    for candidate in candidates:
        key = code_key(candidate)
        if key and key in metrics_by_code:
            return key
    return code_key(next((item for item in candidates if item not in (None, "")), ""))


def ensure_compatible_schema(conn) -> None:
    base.ensure_schema(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(universe)")}
    if "company_name" not in columns:
        conn.execute("ALTER TABLE universe ADD COLUMN company_name TEXT")
    conn.execute(
        "UPDATE universe SET company_name=company WHERE company_name IS NULL OR company_name=''"
    )
    conn.commit()


def metric_criterion(
    name: str,
    value: float | None,
    limit: str,
    approved: bool,
    note: str,
) -> dict:
    return {
        "name": name,
        "value": base.fmt_pct(value),
        "limit": limit,
        "status": "PENDENTE" if value is None else ("APROVADO" if approved else "REPROVADO"),
        "note": note,
        "essential": True,
    }


def update_financial_quality(conn, company, regulatory, *, insurer: bool) -> str:
    fundamental = conn.execute(
        "SELECT * FROM fundamentals WHERE cnpj=?",
        (company["cnpj"],),
    ).fetchone()
    if not fundamental:
        return quality_rules.PENDING_SECTOR
    try:
        existing = json.loads(fundamental["criteria_json"] or "[]")
    except json.JSONDecodeError:
        existing = []
    criteria = [
        item
        for item in existing
        if base.norm(item.get("name")) not in REGULATORY_CRITERIA
    ]
    for item in criteria:
        if base.norm(item.get("name")) in {"DIVIDA LIQUIDA EBITDA", "MARGEM LIQUIDA"}:
            item["essential"] = False
            item["status"] = "INFORMATIVO"
            item["note"] = (
                "Métrica industrial não integra o filtro de instituições financeiras."
            )

    if insurer:
        criteria.append(
            {
                "name": "Solvência e capital SUSEP",
                "value": "Pendente",
                "limit": "Dados individuais conciliados",
                "status": "PENDENTE",
                "note": "O IFData não substitui a supervisão prudencial individual da SUSEP.",
                "essential": True,
            }
        )
    else:
        basel = regulatory.get("basel")
        efficiency = regulatory.get("efficiency")
        npl = regulatory.get("npl")
        coverage = regulatory.get("coverage")
        criteria.extend(
            [
                metric_criterion(
                    "Índice de Basileia",
                    basel,
                    "≥ 10,50%",
                    basel is not None and basel >= BASEL_MIN,
                    "Mínimo regulamentar de referência do Banco Central, inclusive ACP.",
                ),
                metric_criterion(
                    "Eficiência operacional",
                    efficiency,
                    "≤ 60,00% (limite metodológico)",
                    efficiency is not None and efficiency <= EFFICIENCY_MAX,
                    "Quanto menor, melhor; o limite é uma régua educacional, não regulatória.",
                ),
                metric_criterion(
                    "Inadimplência acima de 90 dias",
                    npl,
                    "≤ 5,00% (limite metodológico)",
                    npl is not None and npl <= NPL_MAX,
                    "Régua educacional ampla; a comparação por carteira continua necessária.",
                ),
                metric_criterion(
                    "Cobertura da inadimplência",
                    coverage,
                    "≥ 100,00% (limite metodológico)",
                    coverage is not None and coverage >= COVERAGE_MIN,
                    "Usada como aproximação da suficiência de provisões.",
                ),
                {
                    "name": "Capital principal",
                    "value": base.fmt_pct(regulatory.get("capital")),
                    "limit": "Contexto regulatório",
                    "status": "INFORMATIVO" if regulatory.get("capital") is not None else "PENDENTE",
                    "note": "Exibido para contexto; o gate prudencial usa o Índice de Basileia.",
                    "essential": False,
                },
            ]
        )

    old_reason = base.norm(fundamental["reason"])
    structural_red = any(
        marker in old_reason
        for marker in (
            "PREJUIZO RECORRENTE",
            "DIVIDA EM DETERIORACAO ACELERADA",
            "DETERIORACAO ESTRUTURAL",
        )
    )
    result = quality_rules.classify_quality(
        criteria,
        structural_red=structural_red,
        sector_pending=True,
    )
    evaluated = result.approved + result.rejected
    score = round(100 * result.approved / evaluated, 1) if evaluated else None
    reason = quality_rules.quality_reason(
        result,
        structural_red=structural_red,
        sector_pending=True,
    )
    conn.execute(
        """
        UPDATE fundamentals
           SET quality_status=?,quality_score=?,failures=?,pending=?,reason=?,
               criteria_json=?,updated_at=?
         WHERE cnpj=?
        """,
        (
            result.status,
            score,
            result.rejected,
            result.pending,
            reason,
            json.dumps(criteria, ensure_ascii=False, separators=(",", ":")),
            datetime.now(timezone.utc).isoformat(),
            company["cnpj"],
        ),
    )
    return result.status


def run() -> int:
    conn = app.connect()
    ensure_compatible_schema(conn)
    client = base.session()
    try:
        period, registry = base.latest_period(client)
        report_rows: list[dict] = []
        report_errors: list[str] = []
        for report in base.REPORTS:
            try:
                rows = base.fetch_report(client, period, report)
                report_rows.extend(rows)
                print(f"IFData {period} relatório {report}: {len(rows)} linhas")
            except Exception as exc:
                report_errors.append(f"{report}: {exc!r}")
                print(f"Aviso IFData relatório {report}: {exc}")
        metrics_by_code = normalized_metrics(base.extract_metrics(report_rows))

        all_companies = conn.execute(
            """
            SELECT ticker,cnpj,company_name,trading_name,segment
              FROM universe
             WHERE principal='SIM'
            """
        ).fetchall()
        financial_companies = [
            row for row in all_companies if is_bank(row) or is_insurer(row)
        ]

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("DELETE FROM financial_regulatory")
        banks = insurers = mapped_institutions = mapped_metrics = 0
        quality_counts: dict[str, int] = {}
        pending: list[dict] = []

        for company in financial_companies:
            if is_insurer(company):
                insurers += 1
                conn.execute(
                    "INSERT INTO financial_regulatory VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        company["ticker"], period, "", company["company_name"], None,
                        None, None, None, None, None, None, None,
                        "PENDENTE SUSEP — IFData não cobre seguradora como banco",
                        base.BASE, now,
                    ),
                )
                financial_status = update_financial_quality(
                    conn,
                    company,
                    {},
                    insurer=True,
                )
                quality_counts[financial_status] = quality_counts.get(financial_status, 0) + 1
                pending.append({"ticker": company["ticker"], "status": "PENDENTE SUSEP"})
                continue

            banks += 1
            ranked = sorted(
                ((registry_score(company, item), item) for item in registry),
                key=lambda pair: pair[0],
                reverse=True,
            )
            score, match = ranked[0] if ranked else (0.0, {})
            company_root = digits(company["cnpj"])[:8]
            leader_root = digits(match.get("CnpjInstituicaoLider"))[:8]
            exact_cnpj = bool(company_root and leader_root and company_root == leader_root)
            acceptable = exact_cnpj or score >= 0.52
            code = report_code(match, metrics_by_code) if acceptable else ""
            values = metrics_by_code.get(code, {}) if code else {}

            if acceptable:
                mapped_institutions += 1
            if values:
                mapped_metrics += 1

            basel = plausible_ratio("basel", base.normalize_ratio(values.get("basel")))
            capital = plausible_ratio("capital", base.normalize_ratio(values.get("capital")))
            tier1 = plausible_ratio("tier1", base.normalize_ratio(values.get("tier1")))
            efficiency = plausible_ratio("efficiency", base.normalize_ratio(values.get("efficiency")))
            npl = plausible_ratio("npl", base.normalize_ratio(values.get("npl")))
            coverage = plausible_ratio("coverage", base.normalize_ratio(values.get("coverage")))

            if values:
                status = "VALIDADO IFData — conglomerado prudencial"
            elif acceptable:
                status = "INSTITUIÇÃO MAPEADA — métricas não localizadas nos relatórios consultados"
            else:
                status = "PENDENTE MAPEAMENTO IFData"
            if not values:
                pending.append(
                    {
                        "ticker": company["ticker"],
                        "status": status,
                        "bestMatch": match.get("NomeInstituicao", ""),
                        "score": round(float(score), 4),
                        "reportCode": code,
                    }
                )

            conn.execute(
                "INSERT INTO financial_regulatory VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    company["ticker"], period, code,
                    match.get("NomeInstituicao", "") if acceptable else "",
                    score, basel, capital, tier1, efficiency, npl, coverage,
                    values.get("provisions"), status, base.BASE, now,
                ),
            )
            financial_status = update_financial_quality(
                conn,
                company,
                {
                    "basel": basel,
                    "capital": capital,
                    "tier1": tier1,
                    "efficiency": efficiency,
                    "npl": npl,
                    "coverage": coverage,
                    "provisions": values.get("provisions"),
                },
                insurer=False,
            )
            quality_counts[financial_status] = quality_counts.get(financial_status, 0) + 1
        conn.commit()

        data_path = app.DOCS_DIR / "data.json"
        payload = json.loads(data_path.read_text(encoding="utf-8"))
        regulatory = {
            row["ticker"]: row
            for row in conn.execute("SELECT * FROM financial_regulatory")
        }
        fundamentals = {
            row["cnpj"]: row
            for row in conn.execute(
                "SELECT cnpj,quality_status,quality_score,failures,pending,reason,criteria_json FROM fundamentals"
            )
        }
        for item in payload.get("items", []):
            row = regulatory.get(item.get("ticker"))
            if not row:
                continue
            item.update(
                {
                    "ifdataPeriodo": str(row["period"] or ""),
                    "ifdataInstituicao": row["institution_name"],
                    "ifdataStatus": row["status"],
                    "basileia": base.fmt_pct(row["basel"]),
                    "basileiaRaw": row["basel"],
                    "capitalPrincipal": base.fmt_pct(row["capital_principal"]),
                    "nivel1": base.fmt_pct(row["tier1"]),
                    "eficiencia": base.fmt_pct(row["efficiency"]),
                    "inadimplencia": base.fmt_pct(row["npl"]),
                    "cobertura": base.fmt_pct(row["coverage"]),
                    "fonteRegulatoria": base.SOURCE,
                }
            )
            fundamental = fundamentals.get(item.get("cnpj"))
            if fundamental:
                try:
                    criteria = json.loads(fundamental["criteria_json"] or "[]")
                except json.JSONDecodeError:
                    criteria = []
                item.update(
                    {
                        "filtroQualidade": fundamental["quality_status"],
                        "filtroQualidadeOriginal": fundamental["quality_status"],
                        "scoreQualidade": fundamental["quality_score"],
                        "falhas": fundamental["failures"],
                        "pendencias": fundamental["pending"],
                        "motivoQualidade": fundamental["reason"],
                        "criterios": criteria,
                    }
                )
        payload["ifdata"] = {
            "period": period,
            "banks": banks,
            "insurers": insurers,
            "institutionsMapped": mapped_institutions,
            "metricsMapped": mapped_metrics,
            "pending": pending,
            "reportErrors": report_errors,
            "source": base.SOURCE,
            "updatedAt": now,
            "qualityCounts": quality_counts,
        }
        payload.setdefault("methodology", {})["financial"] = (
            "Bancos: ROE, crescimento, payout e IFData por conglomerado prudencial. "
            "Basileia usa mínimo regulamentar de 10,50%; eficiência ≤ 60%, inadimplência ≤ 5% "
            "e cobertura ≥ 100% são limites metodológicos explícitos. Dado essencial ausente "
            "mantém o filtro pendente. Seguradoras permanecem pendentes até integração SUSEP."
        )
        data_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        status_path = app.DOCS_DIR / "status.json"
        status_payload = (
            json.loads(status_path.read_text(encoding="utf-8"))
            if status_path.exists()
            else {}
        )
        status_payload.update(
            {
                "ifdataPeriod": period,
                "ifdataBanks": banks,
                "ifdataInsurers": insurers,
                "ifdataInstitutionsMapped": mapped_institutions,
                "ifdataMetricsMapped": mapped_metrics,
                "ifdataPending": len(pending),
                "ifdataUpdatedAt": now,
            }
        )
        status_path.write_text(
            json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        app.log(
            conn,
            "OK" if mapped_metrics else "PARCIAL",
            f"IFData {period}: {mapped_metrics}/{banks} bancos com métricas; {insurers} seguradoras pendentes SUSEP.",
        )
        print(
            json.dumps(
                {
                    "status": "OK" if mapped_metrics else "PARCIAL",
                    "period": period,
                    "banks": banks,
                    "insurers": insurers,
                    "institutions_mapped": mapped_institutions,
                    "metrics_mapped": mapped_metrics,
                    "pending": len(pending),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        app.log(conn, "ERRO", f"IFData v2: {exc!r}")
        print(f"ERRO IFData v2: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
