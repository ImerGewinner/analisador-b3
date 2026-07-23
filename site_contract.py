from __future__ import annotations

from typing import Any

import quality_rules


BAZIN_FIELDS = (
    "precoTetoBazinRaw",
    "margemBazinRaw",
)
DCF_FIELDS = (
    "fcfMedio3ARaw",
    "cagrFcf3ARaw",
    "crescimentoDcfRaw",
    "valorFirmaDcfRaw",
    "valorPatrimonioDcfRaw",
    "valorIntrinsecoDcfRaw",
    "precoJustoDcfRaw",
    "margemDcfRaw",
)


def _clear_valuation(item: dict[str, Any]) -> None:
    for field in BAZIN_FIELDS:
        item[field] = None
    item.update(
        {
            "precoTetoBazin": "Bloqueado",
            "margemBazin": "—",
            "statusBazin": "BLOQUEADO",
        }
    )
    for field in DCF_FIELDS:
        item[field] = None
    item.update(
        {
            "fcfMedio3A": "—",
            "cagrFcf3A": "—",
            "crescimentoDcf": "—",
            "valorFirmaDcf": "—",
            "valorPatrimonioDcf": "—",
            "valorIntrinsecoDcf": "—",
            "precoJustoDcf": "—",
            "margemDcf": "—",
            "statusDcf": (
                "NÃO APLICÁVEL — setor financeiro"
                if item.get("financeira")
                else "Valuation bloqueado pelo filtro de qualidade."
            ),
        }
    )


def enforce_item(item: dict[str, Any]) -> None:
    criteria = item.get("criterios") or []
    old_status = str(item.get("filtroQualidadeOriginal") or "")
    structural_red = old_status == quality_rules.RED_ALERT or "deteriora" in str(
        item.get("motivoQualidade") or ""
    ).lower()
    result = quality_rules.classify_quality(
        criteria,
        structural_red=structural_red,
        sector_pending=bool(item.get("financeira")),
    )
    item["filtroQualidadeOriginal"] = result.status
    item["filtroQualidade"] = (
        result.status
        if item.get("elegivelInicial") == "SIM"
        else "FORA DO UNIVERSO INICIAL"
    )
    item["falhas"] = result.rejected
    item["pendencias"] = result.pending
    item["scoreQualidade"] = (
        round(100 * result.approved / (result.approved + result.rejected), 1)
        if result.approved + result.rejected
        else None
    )
    item["motivoQualidade"] = quality_rules.quality_reason(
        result,
        structural_red=structural_red,
        sector_pending=bool(item.get("financeira")),
    )

    if not quality_rules.is_approved(result.status) or item.get("elegivelInicial") != "SIM":
        item["valuationStatus"] = (
            "Valuation bloqueado pelo filtro de qualidade."
            if item.get("elegivelInicial") == "SIM"
            else "Valuation bloqueado — fora do filtro de liquidez."
        )
        _clear_valuation(item)
        item["classificacao3P"] = quality_rules.NOT_CLASSIFIED
        item["justificativaClassificacao"] = (
            "A classificação dos três pilares exige aprovação integral no filtro de qualidade."
        )
    elif item.get("margemBazinRaw") is not None:
        item["statusBazin"] = quality_rules.bazin_band(float(item["margemBazinRaw"]))


def enforce_payload(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items") or []
    for item in items:
        enforce_item(item)

    liquid = [item for item in items if item.get("elegivelInicial") == "SIM"]
    payload["qualityApproved"] = sum(
        item.get("filtroQualidadeOriginal") == quality_rules.APPROVED for item in liquid
    )
    payload["qualityRejected"] = sum(
        item.get("filtroQualidadeOriginal") == quality_rules.REJECTED for item in liquid
    )
    payload["redAlerts"] = sum(
        item.get("filtroQualidadeOriginal") == quality_rules.RED_ALERT for item in liquid
    )
    payload["pendingFundamentals"] = sum(
        quality_rules.is_pending(item.get("filtroQualidadeOriginal")) for item in liquid
    )
    payload["qualityPartialApproved"] = 0
    payload["bazinEnabled"] = sum(
        item.get("precoTetoBazinRaw") is not None for item in liquid
    )
    payload["dcfEnabled"] = sum(
        item.get("precoJustoDcfRaw") is not None for item in liquid
    )
    payload["bazinMarginGe10"] = sum(
        item.get("margemBazinRaw") is not None
        and float(item["margemBazinRaw"]) >= 0.10
        for item in liquid
    )
    payload["dcfMarginGe10"] = sum(
        item.get("margemDcfRaw") is not None
        and float(item["margemDcfRaw"]) >= 0.10
        for item in liquid
    )
    payload.pop("bazinAttractive", None)
    payload.pop("dcfAttractive", None)
    payload["contractVersion"] = "strict-quality-v1"
    return payload


def contract_errors(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for item in payload.get("items") or []:
        ticker = item.get("ticker", "?")
        status = item.get("filtroQualidadeOriginal")
        released = (
            quality_rules.is_approved(status)
            and item.get("elegivelInicial") == "SIM"
        )
        if released:
            essential = [c for c in item.get("criterios") or [] if c.get("essential", True) is not False]
            if any(str(c.get("status", "")).upper() != "APROVADO" for c in essential):
                errors.append(f"{ticker}: aprovado com critério essencial não aprovado")
        elif item.get("precoTetoBazinRaw") is not None or item.get("precoJustoDcfRaw") is not None:
            errors.append(f"{ticker}: valuation presente sem filtro e liquidez aprovados")
        if not released and item.get("classificacao3P") != quality_rules.NOT_CLASSIFIED:
            errors.append(f"{ticker}: classificação positiva sem filtro e liquidez aprovados")
    return errors
