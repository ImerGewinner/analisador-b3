import copy
import unittest

import quality_rules
from site_contract import contract_errors, enforce_payload


class SiteContractTests(unittest.TestCase):
    def test_tris_like_single_failure_cannot_keep_valuation(self):
        payload = {
            "items": [
                {
                    "ticker": "TRIS3",
                    "elegivelInicial": "SIM",
                    "financeira": False,
                    "filtroQualidadeOriginal": "APROVADA NO FILTRO",
                    "motivoQualidade": "1 reprovação",
                    "criterios": [
                        {"name": "ROE médio 5A", "status": "REPROVADO"},
                        {"name": "Crescimento", "status": "APROVADO"},
                        {"name": "Payout", "status": "APROVADO"},
                    ],
                    "precoTetoBazinRaw": 10.0,
                    "precoJustoDcfRaw": 9.0,
                    "margemBazinRaw": 1.0,
                    "margemDcfRaw": 0.8,
                    "classificacao3P": "High Quality Value",
                }
            ]
        }
        enforced = enforce_payload(copy.deepcopy(payload))
        item = enforced["items"][0]
        self.assertEqual(item["filtroQualidadeOriginal"], quality_rules.REJECTED)
        self.assertIsNone(item["precoTetoBazinRaw"])
        self.assertIsNone(item["precoJustoDcfRaw"])
        self.assertEqual(item["classificacao3P"], quality_rules.NOT_CLASSIFIED)
        self.assertEqual(contract_errors(enforced), [])

    def test_approved_quality_outside_liquidity_cannot_keep_valuation(self):
        payload = {
            "items": [
                {
                    "ticker": "TEST3",
                    "elegivelInicial": "NÃO",
                    "financeira": False,
                    "filtroQualidadeOriginal": quality_rules.APPROVED,
                    "criterios": [
                        {"name": "Qualidade", "status": "APROVADO"},
                    ],
                    "precoTetoBazinRaw": 10.0,
                    "precoJustoDcfRaw": 9.0,
                    "classificacao3P": "Qualidade aprovada",
                }
            ]
        }
        enforced = enforce_payload(payload)
        item = enforced["items"][0]
        self.assertIsNone(item["precoTetoBazinRaw"])
        self.assertIsNone(item["precoJustoDcfRaw"])
        self.assertEqual(item["classificacao3P"], quality_rules.NOT_CLASSIFIED)
        self.assertEqual(contract_errors(enforced), [])


if __name__ == "__main__":
    unittest.main()
