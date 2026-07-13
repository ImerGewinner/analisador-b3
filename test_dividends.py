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


if __name__ == "__main__":
    unittest.main()
