import unittest

import financial_sector as sector


class FinancialSectorTests(unittest.TestCase):
    def test_normalize_ratio(self):
        self.assertAlmostEqual(sector.normalize_ratio(15.5), 0.155)
        self.assertAlmostEqual(sector.normalize_ratio(0.155), 0.155)

    def test_metric_identification(self):
        self.assertEqual(sector.metric_score("Índice de Basileia", "basel"), 100)
        self.assertEqual(sector.metric_score("Índice de Eficiência", "efficiency"), 100)
        self.assertGreater(sector.metric_score("Inadimplência acima de 90 dias", "npl"), 0)

    def test_alias_normalization(self):
        self.assertEqual(sector.norm("Banco do Brasil S.A."), "DO")
        self.assertIn("ITAU", sector.norm("Itaú Unibanco Holding S.A."))


if __name__ == "__main__":
    unittest.main()
