import sqlite3
import unittest

import advanced_analysis as advanced


class AdvancedAnalysisTests(unittest.TestCase):
    def test_number_preserves_decimal_point(self):
        self.assertEqual(advanced.number("498091000"), 498091000.0)
        self.assertAlmostEqual(advanced.number("1.234,56"), 1234.56)

    def test_share_class(self):
        self.assertEqual(advanced.share_class_for_ticker("PETR3"), "ON")
        self.assertEqual(advanced.share_class_for_ticker("PETR4"), "PN")
        self.assertEqual(advanced.share_class_for_ticker("TAEE11"), "")

    def test_dividend_history(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE cash_dividends(ticker TEXT,eligible_dpa INTEGER,last_date_prior TEXT,rate REAL)")
        for year in range(2021, 2026):
            conn.execute("INSERT INTO cash_dividends VALUES('TEST3',1,?,1.0)", (f"{year}-06-30",))
        paid, streak, cagr = advanced.dividend_history(conn, "TEST3", "2026-07-17")
        self.assertEqual(paid, 5)
        self.assertEqual(streak, 5)
        self.assertAlmostEqual(cagr, 0.0)

    def test_dilution(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE share_history(cnpj TEXT,year INTEGER,share_class TEXT,estimated_shares REAL)")
        conn.execute("INSERT INTO share_history VALUES('1',2021,'ON',100)")
        conn.execute("INSERT INTO share_history VALUES('1',2025,'ON',120)")
        change, status = advanced.dilution_metric(conn, "1", "ON")
        self.assertAlmostEqual(change, 0.20)
        self.assertEqual(status, "DILUIÇÃO")


if __name__ == "__main__":
    unittest.main()
