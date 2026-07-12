from __future__ import annotations

import fundamentals


def cvm_number(value: object, scale: object = "") -> float | None:
    """Converte VL_CONTA da CVM sem remover o separador decimal.

    Os arquivos DFP/ITR normalmente usam ponto decimal mesmo com CSV separado
    por ponto e vírgula. A implementação anterior removia todos os pontos e
    inflava os valores, por exemplo, em 10.000 vezes quando a escala era MIL.
    """
    raw = str(value or "").strip().replace(" ", "")
    if not raw:
        return None

    try:
        if "," in raw and "." in raw:
            # O separador que aparece por último é tratado como decimal.
            if raw.rfind(",") > raw.rfind("."):
                normalized = raw.replace(".", "").replace(",", ".")
            else:
                normalized = raw.replace(",", "")
        elif "," in raw:
            normalized = raw.replace(",", ".")
        else:
            normalized = raw
        result = float(normalized)
    except ValueError:
        return None

    scale_text = fundamentals.norm_text(scale)
    if "MILHAO" in scale_text:
        result *= 1_000_000
    elif "MIL" in scale_text:
        result *= 1_000
    return result


# parse_package consulta a função number no namespace do módulo fundamentals.
fundamentals.number = cvm_number

import app  # noqa: E402  (importação posterior ao patch é intencional)


if __name__ == "__main__":
    raise SystemExit(app.run())
