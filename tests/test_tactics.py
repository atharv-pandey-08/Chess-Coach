"""Tactic detector tests: each position contains exactly the motif named in the test."""

import unittest

import chess

from chess_coach import tactics as t


def kinds(fen: str, san: str, **kw) -> list[str]:
    board = chess.Board(fen)
    return [m.kind for m in t.motifs(board, board.parse_san(san), **kw)]


class MotifTests(unittest.TestCase):
    def test_knight_fork(self):
        self.assertIn("fork", kinds("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1", "Nc7+"))

    def test_fork_text_names_both_targets(self):
        board = chess.Board("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1")
        fork = next(m for m in t.motifs(board, board.parse_san("Nc7+")) if m.kind == "fork")
        self.assertIn("king on e8", fork.text)
        self.assertIn("rook on a8", fork.text)

    def test_defended_square_is_not_a_fork(self):
        # Same fork, but a black bishop on d8 covers c7: the knight just gets taken.
        self.assertNotIn("fork", kinds("r2bk3/8/8/1N6/8/8/8/4K3 w - - 0 1", "Nc7+"))

    def test_absolute_pin(self):
        self.assertIn("pin", kinds("4k3/8/2n5/8/8/8/8/4KB2 w - - 0 1", "Bb5"))

    def test_skewer(self):
        self.assertIn("skewer", kinds("4q3/8/8/8/4k3/8/8/R5K1 w - - 0 1", "Re1+"))

    def test_discovered_attack(self):
        self.assertIn("discovered_attack", kinds("4q1k1/8/8/8/4N3/8/8/4R1K1 w - - 0 1", "Nf6+"))

    def test_back_rank_mate(self):
        self.assertEqual(kinds("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "Ra8#"), ["back_rank_mate"])

    def test_mate_threat(self):
        board = chess.Board()
        for san in ("e4", "e5", "Bc4", "Nc6"):
            board.push_san(san)
        self.assertIn("mate_threat", [m.kind for m in t.motifs(board, board.parse_san("Qh5"))])

    def test_free_capture(self):
        self.assertIn("wins_material", kinds("4k3/8/8/3n4/8/8/8/3QK3 w - - 0 1", "Qxd5"))

    def test_trade_is_not_winning_material(self):
        self.assertNotIn("wins_material", kinds("4k3/4p3/3n4/8/8/8/8/3QK1N1 w - - 0 1", "Qxd6"))

    def test_hangs_detects_loose_piece(self):
        # Bb5+ puts the bishop where the a6 pawn simply takes it.
        self.assertIn("hangs", kinds("4k3/8/p7/8/2B5/8/8/4K3 w - - 0 1", "Bb5+", include_hangs=True))


class ExchangeTests(unittest.TestCase):
    def test_see_defended_pawn_loses_the_queen(self):
        board = chess.Board("4k3/8/2p5/3p4/8/8/8/3QK3 w - - 0 1")
        self.assertEqual(t.see(board, board.parse_san("Qxd5")), -8)

    def test_see_undefended_knight(self):
        board = chess.Board("4k3/8/8/3n4/8/8/8/3QK3 w - - 0 1")
        self.assertEqual(t.see(board, board.parse_san("Qxd5")), 3)

    def test_hanging_list(self):
        board = chess.Board("4k3/8/8/3n4/8/8/8/3QK3 b - - 0 1")
        squares = [chess.square_name(sq) for sq, _, _ in t.hanging(board, chess.BLACK)]
        self.assertEqual(squares, ["d5"])

    def test_material_balance(self):
        self.assertEqual(t.material(chess.Board(), chess.WHITE), 0)


if __name__ == "__main__":
    unittest.main()
