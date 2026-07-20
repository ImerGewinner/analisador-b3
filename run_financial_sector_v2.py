from __future__ import annotations

import re

import app
import financial_sector


_original_connect = app.connect


def connect_compatible():
    conn = _original_connect()
    conn.create_function(
        "REGEXP",
        2,
        lambda pattern, value: int(
            bool(re.search(str(pattern or ""), str(value or ""), flags=re.IGNORECASE))
        ),
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(universe)")}
    if "company_name" not in existing:
        conn.execute("ALTER TABLE universe ADD COLUMN company_name TEXT")
    conn.execute("UPDATE universe SET company_name=company WHERE company_name IS NULL OR company_name='' ")
    conn.commit()
    return conn


if __name__ == "__main__":
    app.connect = connect_compatible
    raise SystemExit(financial_sector.run())
