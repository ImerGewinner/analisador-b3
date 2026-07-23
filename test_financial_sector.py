import unittest

import financial_sector as sector
import financial_sector_v2 as sector_v2
import fundamentals


class FinancialSectorTests(unittest.TestCase):
    def test_normalize_ratio(self):
        self.assertAlmostEqual(sector.normalize_ratio(15.5), 0.155)
        self.assertAlmostEqual(sector.normalize_ratio(0.155), 0.155)

    def test_metric_identification(self):
        self.assertGreater(sector.metric_score("Índice de Basileia", "basel"), 0)
        self.assertGreater(
            sector.metric_score("Índice de Eficiência", "efficiency"), 0
        )
        self.assertGreater(
            sector.metric_score("Inadimplência acima de 90 dias", "npl"), 0
        )

    def test_alias_normalization(self):
        normalized = sector.norm("Banco do Brasil S.A.")
        self.assertIn("DO", normalized)
        self.assertNotIn("BANCO", normalized)
        self.assertIn("ITAU", sector.norm("Itaú Unibanco Holding S.A."))

    def test_legal_name_does_not_override_health_segment(self):
        metadata = {
            "segment": "Serv.Méd.Hospit..Análises e Diagnósticos",
            "company": "QUALICORP CONSULTORIA E CORRETORA DE SEGUROS S.A.",
        }
        self.assertFalse(fundamentals.is_financial_company(metadata))

    def test_insurance_broker_is_not_prudential_insurer(self):
        company = {
            "ticker": "WIZC3",
            "segment": "Corretoras de Seguros e Resseguros",
        }
        self.assertFalse(sector_v2.is_insurer(company))

    def test_impossible_ifdata_ratio_is_pending(self):
        self.assertIsNone(sector_v2.plausible_ratio("npl", 329_260_614.0))
        self.assertAlmostEqual(sector_v2.plausible_ratio("basel", 0.149), 0.149)


if __name__ == "__main__":
    unittest.main()
