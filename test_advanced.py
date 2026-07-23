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
        conn.execute(
            """CREATE TABLE cash_dividends(
            ticker TEXT,eligible_dpa INTEGER,label TEXT,approved_on TEXT,
            last_date_prior TEXT,payment_date TEXT,rate REAL,related_to TEXT,remarks TEXT
            )"""
        )
        for year in range(2021, 2026):
            conn.execute(
                "INSERT INTO cash_dividends VALUES('TEST3',1,'DIVIDENDO',? ,?,'',1.0,'','')",
                (f"{year}-06-20", f"{year}-06-30"),
            )
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

    def test_dcf_discounts_explicit_flows_terminal_and_net_debt(self):
        result = advanced.discounted_fcff_valuation(
            fcf_base=100.0,
            growth=0.05,
            net_debt=200.0,
            shares=100.0,
            price=8.0,
        )
        explicit = sum(
            100.0 * (1.05 ** year) / (1.12 ** year)
            for year in range(1, 11)
        )
        terminal_10 = 100.0 * (1.05 ** 10) * 1.03 / (0.12 - 0.03)
        terminal_pv = terminal_10 / (1.12 ** 10)
        enterprise = explicit + terminal_pv
        equity = enterprise - 200.0
        self.assertAlmostEqual(result["present_value_explicit"], explicit)
        self.assertAlmostEqual(result["present_value_terminal"], terminal_pv)
        self.assertAlmostEqual(result["enterprise_value"], enterprise)
        self.assertAlmostEqual(result["equity_value"], equity)
        self.assertAlmostEqual(result["reference_price"], equity / 100.0 * 0.75)

    def test_series_cagr_requires_positive_endpoints(self):
        self.assertAlmostEqual(advanced.series_cagr([100.0, 110.0, 121.0]), 0.10)
        self.assertIsNone(advanced.series_cagr([-1.0, 2.0, 3.0]))


if __name__ == "__main__":
    unittest.main()
