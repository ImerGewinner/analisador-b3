import sqlite3
import unittest

import dividends
import run_dividends


class DividendsTests(unittest.TestCase):
    def test_parse_decimal_brazilian(self):
        self.assertAlmostEqual(dividends.parse_decimal("1,234567"), 1.234567)
        self.assertAlmostEqual(dividends.parse_decimal("1.234,56"), 1234.56)
        self.assertAlmostEqual(dividends.parse_decimal(0.75), 0.75)

    def test_parse_integer_shares(self):
        self.assertEqual(dividends.parse_integer("13.445.090.232"), 13445090232)

    def test_dividend_and_jcp_are_eligible(self):
        self.assertTrue(dividends.is_dpa_event("DIVIDENDO"))
        self.assertTrue(dividends.is_dpa_event("Juros sobre Capital Próprio"))
        self.assertTrue(run_dividends.is_dpa_event_compatible("JRS CAP PROPRIO"))
        self.assertFalse(dividends.is_dpa_event("Restituição de capital"))
        self.assertFalse(dividends.is_dpa_event("Bonificação"))

    def test_share_class_mapping(self):
        self.assertEqual(run_dividends.share_type_for_ticker("VALE3"), "ON")
        self.assertEqual(run_dividends.share_type_for_ticker("PETR4"), "PN")
        self.assertEqual(run_dividends.share_type_for_ticker("ALUP11"), "UNT")

    def test_bazin_formula(self):
        dpa = 3.10
        price = 35.00
        ceiling = dpa / dividends.BAZIN_YIELD
        margin = ceiling / price - 1
        self.assertAlmostEqual(ceiling, 40.0)
        self.assertAlmostEqual(margin, 40.0 / 35.0 - 1)

    def test_payout_threshold_is_strict(self):
        self.assertTrue(0.8999 < dividends.PAYOUT_MAX)
        self.assertFalse(0.90 < dividends.PAYOUT_MAX)

    def test_near_identical_b3_events_are_deduplicated(self):
        base = {
            "label": "DIVIDENDO",
            "approved_on": "2025-12-19",
            "last_date_prior": "2025-12-26",
            "payment_date": "",
            "related_to": "",
            "remarks": "Fonte B3",
        }
        events = [
            {**base, "rate": 0.1427447908},
            {**base, "rate": 0.1427447909},
        ]
        self.assertEqual(len(dividends.deduplicate_events(events)), 1)

    def test_unexplained_dividend_outlier_blocks_integrity(self):
        status, valid = dividends.dividend_integrity(0.70, 0.0, 0.10)
        self.assertFalse(valid)
        self.assertTrue(status.startswith("PENDENTE"))

    def test_bazin_labels_are_neutral(self):
        self.assertEqual(dividends.quality_rules.bazin_band(0.15), "MARGEM ≥ +10%")
        self.assertEqual(dividends.quality_rules.bazin_band(-0.01), "MARGEM NEGATIVA")

    def test_impossible_regulatory_ratio_is_not_exposed(self):
        self.assertIsNone(dividends.financial_ratio(32_926_061_427.54, 1.0))
        self.assertAlmostEqual(dividends.financial_ratio(0.149, 1.0), 0.149)

    def test_sibling_class_event_does_not_become_zero_dpa(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE cash_dividends(
            root TEXT,ticker TEXT,eligible_dpa INTEGER,last_date_prior TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO cash_dividends VALUES('PETR','PETR3',1,'2026-05-01')"
        )
        self.assertTrue(
            dividends.has_sibling_class_events(
                conn, "PETR", "PETR4", "2025-07-21", "2026-07-21"
            )
        )


if __name__ == "__main__":
    unittest.main()
