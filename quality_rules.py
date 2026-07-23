from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


APPROVED = "APROVADA NO FILTRO"
REJECTED = "REPROVADA NO FILTRO"
PENDING = "PENDENTE"
PENDING_SECTOR = "PENDENTE — DADOS SETORIAIS"
RED_ALERT = "ALERTA VERMELHO"
NOT_CLASSIFIED = "Não classificada — filtro não aprovado"


@dataclass(frozen=True)
class QualityResult:
    status: str
    approved: int
    rejected: int
    pending: int


def _essential(criterion: Mapping[str, object]) -> bool:
    return criterion.get("essential", True) is not False


def classify_quality(
    criteria: Iterable[Mapping[str, object]],
    *,
    structural_red: bool = False,
    sector_pending: bool = False,
) -> QualityResult:
    """Apply the quality gate without allowing partial approvals.

    Non-essential, informational criteria do not affect the gate. Exactly one
    failed essential criterion is enough to reject the company. Missing data
    only becomes the final state when there are no failures.
    """

    relevant = [criterion for criterion in criteria if _essential(criterion)]
    approved = sum(str(item.get("status", "")).upper() == "APROVADO" for item in relevant)
    rejected = sum(str(item.get("status", "")).upper() == "REPROVADO" for item in relevant)
    pending = sum(
        str(item.get("status", "")).upper() not in {"APROVADO", "REPROVADO"}
        for item in relevant
    )

    if structural_red or rejected >= 2:
        status = RED_ALERT
    elif rejected == 1:
        status = REJECTED
    elif pending:
        status = PENDING_SECTOR if sector_pending else PENDING
    else:
        status = APPROVED
    return QualityResult(status, approved, rejected, pending)


def is_approved(status: object) -> bool:
    return str(status or "").strip().upper() == APPROVED


def is_pending(status: object) -> bool:
    return str(status or "").strip().upper().startswith(PENDING)


def valuation_block_reason(status: object, eligible: object = "SIM") -> str | None:
    if str(eligible or "").upper() != "SIM":
        return "Valuation bloqueado — fora do filtro de liquidez."
    if is_approved(status):
        return None
    return "Valuation bloqueado pelo filtro de qualidade."


def quality_reason(
    result: QualityResult,
    *,
    structural_red: bool = False,
    sector_pending: bool = False,
) -> str:
    reasons: list[str] = []
    if result.rejected:
        reasons.append(f"{result.rejected} critério(s) reprovado(s)")
    if structural_red:
        reasons.append("deterioração estrutural identificada")
    if result.pending:
        label = "dado(s) setorial(is) pendente(s)" if sector_pending else "critério(s) pendente(s)"
        reasons.append(f"{result.pending} {label}")
    if not reasons:
        reasons.append("zero reprovações e zero pendências essenciais")
    return "; ".join(reasons)


def bazin_band(margin: float) -> str:
    if margin >= 0.10:
        return "MARGEM ≥ +10%"
    if margin >= 0:
        return "MARGEM ENTRE 0% E +10%"
    return "MARGEM NEGATIVA"


def blocked_classification(status: object) -> tuple[str, str] | None:
    if is_approved(status):
        return None
    return NOT_CLASSIFIED, "A classificação dos três pilares exige aprovação integral no filtro de qualidade."
