"""End-to-end check with a real Stockfish. Skipped when the engine isn't installed."""

import unittest

from chess_coach import analysis, config, facts
from tests.test_analysis import SCHOLARS_MATE

ENGINE = config.find_stockfish()


@unittest.skipUnless(ENGINE, "Stockfish not installed")
class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = analysis.parse_pgn(SCHOLARS_MATE)
        job = analysis.EngineJob(cls.parsed, depth=10)
        job.run(ENGINE)
        cls.job = job
        cls.review = analysis.build_review(SCHOLARS_MATE, job.evals, user_color="black", parsed=cls.parsed)

    def test_every_position_is_evaluated(self):
        self.assertIsNone(self.job.error)
        self.assertTrue(self.review["complete"])
        self.assertEqual(self.review["positions"][-1]["eval"]["text"], "1-0")

    def test_real_engine_finds_the_blunder_and_the_mate(self):
        nf6 = self.review["moves"][5]
        self.assertEqual(nf6["san"], "Nf6")
        self.assertEqual(nf6["classification"], "Blunder")
        self.assertEqual(self.review["moves"][-1]["classification"], "Best")

    def test_fact_sheet_with_engine_threat_search(self):
        engine = facts.CoachEngine(depth=10)
        try:
            f = facts.build_facts(self.parsed, self.job.evals, self.review, 6, user_color="black", engine=engine)
        finally:
            engine.close()
        self.assertTrue(f["refutation"]["ends_in_mate"])
        self.assertEqual(f["threat"]["move"], "Qxf7#")
        self.assertFalse(f["threat"]["addressed"])


if __name__ == "__main__":
    unittest.main()
