from __future__ import annotations

import json
from datetime import datetime, timezone

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

import dividends


_SESSION = dividends.http_session()


def fetch_supplement_compatible(root: str) -> dict:
    """Consulta a API pública da B3 com fallback TLS controlado.

    O endpoint de Companhias Listadas já exigiu desativação da verificação SSL
    em clientes públicos como o pacote rb3. Primeiro tentamos a validação normal;
    se houver erro de certificado, repetimos somente esta chamada com verify=False.
    """
    url = dividends.payload_url(root)
    try:
        response = _SESSION.get(url, timeout=90)
    except requests.exceptions.SSLError:
        urllib3.disable_warnings(InsecureRequestWarning)
        response = _SESSION.get(url, timeout=90, verify=False)

    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        preview = response.text[:180].replace("\n", " ")
        raise RuntimeError(
            f"B3 retornou conteúdo não JSON para {root}: {preview}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"Resposta B3 inválida para {root}: {type(data).__name__}")
    return data


def main() -> int:
    dividends.fetch_supplement = fetch_supplement_compatible
    started = datetime.now(timezone.utc).isoformat()
    print(json.dumps({"etapa": "proventos_b3", "inicio": started}, ensure_ascii=False))
    return dividends.run()


if __name__ == "__main__":
    raise SystemExit(main())
