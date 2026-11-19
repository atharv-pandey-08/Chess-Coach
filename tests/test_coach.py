"""Fact sheet, validator and story tests. No network or Stockfish needed (engine=None)."""

import unittest

from chess_coach import ai_coach, analysis as a, facts, story
from tests.test_analysis import SCHOLARS_MATE, ev


def scholars_review(user_color="black"):
    parsed = a.parse_pgn(SCHOLARS_MATE)
    evals = [ev(cp=30)] * len(parsed.boards)
    evals[5] = ev(cp=20, best="d8e7", pv=["d8e7", "h5f7"])        # before 3...Nf6??
    evals[6] = ev(mate=1, best="h5f7", pv=["h5f7"])               # after it: mate in one
    evals[-1] = a._terminal_eval(parsed.boards[-1])
    review = a.build_review(SCHOLARS_MATE, evals, user_color=user_color, parsed=parsed)
    return parsed, evals, review


class FactSheetTests(unittest.TestCase):
    def setUp(self):
        self.parsed, self.evals, self.review = scholars_review()
        self.f = facts.build_facts(self.parsed, self.evals, self.review, 6, user_color="black")

    def test_refutation_is_the_mate(self):
        self.assertEqual(self.f["refutation"]["san"], ["Qxf7#"])
        self.assertTrue(self.f["refutation"]["ends_in_mate"])
        self.assertIn("checkmate", self.f["key_point"])

    def test_threat_found_without_engine(self):
        # Before 3...Nf6, White already threatened Qxf7# (static mate-in-one fallback).
        self.assertEqual(self.f["threat"]["move"], "Qxf7#")
        self.assertFalse(self.f["threat"]["addressed"])

    def test_best_line_and_player_framing(self):
        self.assertEqual(self.f["best"]["move"], "Qe7")
        self.assertEqual(self.f["move"]["player"], "you")

    def test_vocabulary_lists_only_real_things(self):
        v = self.f["vocabulary"]
        self.assertIn("Qxf7", v["moves"])
        self.assertIn("Qe7", v["moves"])
        self.assertIn("queen", v["pieces"]["h5"])

    def test_rule_based_note(self):
        note = ai_coach.rule_based(self.f)
        self.assertEqual(note["headline"], "This allows a forced checkmate.")
        self.assertIn("Qe7", note["better_idea"])
        self.assertEqual(ai_coach.validate(note, self.f["vocabulary"]), [])


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        parsed, evals, review = scholars_review()
        self.vocab = facts.build_facts(parsed, evals, review, 6, user_color="black")["vocabulary"]
        self.good = {"headline": "This allows mate.", "what_happened": "After Nf6, White plays Qxf7#.",
                     "what_was_missed": "The queen on h5 and bishop on c4 both hit f7.",
                     "better_idea": "Qe7 defends f7.", "lesson": "Watch for forks and pins."}

    def check(self, **changes):
        return ai_coach.validate({**self.good, **changes}, self.vocab)

    def test_accepts_grounded_note(self):
        self.assertEqual(self.check(), [])

    def test_rejects_invented_move(self):
        self.assertTrue(self.check(better_idea="Nxe4 was better."))

    def test_rejects_wrong_piece_on_square(self):
        self.assertTrue(self.check(what_was_missed="The knight on h5 attacks f7."))

    def test_rejects_unfound_tactic_but_allows_generic_lesson(self):
        self.assertTrue(self.check(what_happened="Nf6 walks into a fork."))
        self.assertEqual(self.check(lesson="Always look for forks."), [])

    def test_rejects_engine_numbers(self):
        self.assertTrue(self.check(what_happened="The eval drops to -5.2."))

    def test_rejects_unknown_square(self):
        self.assertTrue(self.check(what_happened="The pawn push h3 was needed."))


class StoryTests(unittest.TestCase):
    def test_turning_point_is_the_blunder(self):
        parsed, evals, review = scholars_review()
        st = story.build_story(parsed, evals, review, user_color="black")
        self.assertEqual([p["san"] for p in st["turning_points"]], ["Nf6"])
        self.assertIn("You lost", st["summary"])


if __name__ == "__main__":
    unittest.main()
