"""Unit tests for the analysis core. No Stockfish or network needed.

Run:  python -m unittest discover tests
"""

import unittest

from chess_coach import analysis as a
from chess_coach.chesscom import game_from_pgn, opening_from_pgn

SCHOLARS_MATE = '[White "W"]\n[Black "B"]\n[Result "1-0"]\n\n1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0'


def ev(cp=None, mate=None, best=None, pv=None):
    """Fake engine evaluation (White's perspective)."""
    wp = a.win_pct_from_cp(cp) if mate is None else (100.0 if mate > 0 else 0.0)
    return {"cp": cp, "mate": mate, "wp": wp, "terminal": None, "best": best,
            "pv": pv or ([best] if best else []), "second_wp": None}


class ScoringTests(unittest.TestCase):
    def test_win_pct_is_symmetric_and_bounded(self):
        self.assertAlmostEqual(a.win_pct_from_cp(0), 50.0)
        self.assertAlmostEqual(a.win_pct_from_cp(300) + a.win_pct_from_cp(-300), 100.0)
        self.assertLess(a.win_pct_from_cp(10**6), 100.01)

    def test_accuracy_curve(self):
        self.assertGreater(a.move_accuracy(0), 99)
        self.assertLess(a.move_accuracy(40), 20)
        self.assertIsNone(a.game_accuracy([]))

    def test_format_eval(self):
        self.assertEqual(a.format_eval(ev(cp=150)), "+1.5")
        self.assertEqual(a.format_eval(ev(mate=-3)), "-M3")


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.parsed = a.parse_pgn(SCHOLARS_MATE)
        n = len(self.parsed.boards)
        self.evals = [ev(cp=30)] * (n - 1)
        # Before the final move White has mate in 1; the final position is checkmate.
        self.evals[-1] = ev(mate=1, best="h5f7")
        self.evals.append(a._terminal_eval(self.parsed.boards[-1]))

    def test_checkmate_is_not_a_blunder(self):
        """Regression: delivering mate used to be scored as a huge loss ("Blunder")."""
        review = a.build_review(SCHOLARS_MATE, self.evals, user_color="white")
        last = review["moves"][-1]
        self.assertEqual(last["san"], "Qxf7#")
        self.assertEqual(last["classification"], "Best")
        self.assertIn("Checkmate", last["comment"])
        self.assertEqual(review["positions"][-1]["eval"]["text"], "1-0")

    def test_illegal_cached_best_move_does_not_crash(self):
        evals = list(self.evals)
        evals[0] = ev(cp=30, best="e7e5")  # illegal for White in the start position
        review = a.build_review(SCHOLARS_MATE, evals, user_color="white")
        self.assertIsNone(review["positions"][0]["eval"]["best_san"])

    def test_partial_evals_are_supported(self):
        partial = self.evals[:3] + [None] * (len(self.evals) - 3)
        review = a.build_review(SCHOLARS_MATE, partial, user_color="black")
        self.assertFalse(review["complete"])
        self.assertIsNotNone(review["moves"][0]["classification"])
        self.assertIsNone(review["moves"][-1]["classification"])
        self.assertFalse(review["moves"][0]["is_user"])  # user is Black; ply 1 is White

    def test_blunder_comment_names_better_move(self):
        evals = list(self.evals)
        # 3...Nf6?? : before it Black is fine (best Qe7), after it White is mating.
        evals[5] = ev(cp=20, best="d8e7")
        evals[6] = ev(mate=1, best="h5f7", pv=["h5f7"])
        review = a.build_review(SCHOLARS_MATE, evals, user_color="black")
        move = review["moves"][5]
        self.assertEqual(move["san"], "Nf6")
        self.assertEqual(move["classification"], "Blunder")
        self.assertIn("Qe7", move["comment"])
        self.assertIn("mate", move["comment"])


class ChessComTests(unittest.TestCase):
    def test_opening_name_from_eco_url(self):
        pgn = '[ECOUrl "https://www.chess.com/openings/Italian-Game-Two-Knights-Defense...4.Ng5"]'
        self.assertEqual(opening_from_pgn(pgn), "Italian Game Two Knights Defense")

    def test_pgn_import(self):
        game = game_from_pgn(SCHOLARS_MATE)
        self.assertEqual(game["result"], "Win")
        self.assertTrue(game["id"].startswith("pgn-"))


if __name__ == "__main__":
    unittest.main()
