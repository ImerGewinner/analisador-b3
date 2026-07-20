from __future__ import annotations

import re

import app
import financial_sector


_original_connect = app.connect


def connect_with_regexp():
    conn = _original_connect()
    conn.create_function(
        "REGEXP",
        2,
        lambda pattern, value: 1
        if re.search(str(pattern or ""), str(value or ""), flags=re.IGNORECASE)
        else 0,
    )
    return conn


if __name__ == "__main__":
    app.connect = connect_with_regexp
    raise SystemExit(financial_sector.run())
