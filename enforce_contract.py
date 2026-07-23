from __future__ import annotations

import json

import app
from site_contract import contract_errors, enforce_payload


def run() -> int:
    data_path = app.DOCS_DIR / "data.json"
    payload = enforce_payload(json.loads(data_path.read_text(encoding="utf-8")))
    errors = contract_errors(payload)
    if errors:
        raise RuntimeError("; ".join(errors[:20]))
    data_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    status_path = app.DOCS_DIR / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    for key in (
        "qualityApproved",
        "qualityRejected",
        "redAlerts",
        "pendingFundamentals",
        "bazinEnabled",
        "dcfEnabled",
        "bazinMarginGe10",
        "dcfMarginGe10",
        "contractVersion",
    ):
        status[key] = payload.get(key)
    status.pop("bazinAttractive", None)
    status.pop("dcfAttractive", None)
    status_path.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "OK",
                "contract": payload["contractVersion"],
                "approved": payload["qualityApproved"],
                "rejected": payload["qualityRejected"],
                "red": payload["redAlerts"],
                "pending": payload["pendingFundamentals"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
