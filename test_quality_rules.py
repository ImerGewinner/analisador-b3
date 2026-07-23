import unittest

import quality_rules


def criterion(status, *, essential=True):
    return {"status": status, "essential": essential}


class QualityRulesTests(unittest.TestCase):
    def test_one_failure_rejects_and_blocks(self):
        result = quality_rules.classify_quality(
            [criterion("APROVADO"), criterion("REPROVADO"), criterion("APROVADO")]
        )
        self.assertEqual(result.status, quality_rules.REJECTED)
        self.assertIsNotNone(quality_rules.valuation_block_reason(result.status))

    def test_failure_has_priority_over_pending(self):
        result = quality_rules.classify_quality(
            [criterion("REPROVADO"), criterion("PENDENTE")]
        )
        self.assertEqual(result.status, quality_rules.REJECTED)

    def test_two_failures_are_red_alert(self):
        result = quality_rules.classify_quality(
            [criterion("REPROVADO"), criterion("REPROVADO")]
        )
        self.assertEqual(result.status, quality_rules.RED_ALERT)

    def test_pending_without_failure_never_approves(self):
        result = quality_rules.classify_quality(
            [criterion("APROVADO"), criterion("PENDENTE")]
        )
        self.assertEqual(result.status, quality_rules.PENDING)

    def test_only_all_essential_approved_releases(self):
        result = quality_rules.classify_quality(
            [criterion("APROVADO"), criterion("APROVADO"), criterion("INFORMATIVO", essential=False)]
        )
        self.assertEqual(result.status, quality_rules.APPROVED)


if __name__ == "__main__":
    unittest.main()
