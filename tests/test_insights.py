"""Cross-game insights, puzzle generation and spaced repetition. No engine or network."""

import unittest

from chess_coach import insights, srs
from tests.test_analysis import SCHOLARS_MATE
from tests.test_coach import scholars_review


def fake_game(gid="g1", color="black", result="Loss", opening="Italian Game Two Knights", end=1000):
    return {"id": gid, "pgn": SCHOLARS_MATE, "user_color": color, "result": result, "opening": opening,
            "opponent": "W", "end_time": end, "time_class": "blitz"}


class CategoryTests(unittest.TestCase):
    def facts(self, **over):
        f = {"move": {"color": "white", "win_chance_before": 50},
             "refutation": {"tactics": [], "san": [], "material_change": 0, "ends_in_mate": False},
             "threat": None, "best": None, "played_tactics": []}
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(f.get(k), dict):
                f[k] = {**f[k], **v}
            else:
                f[k] = v
        return f

    def test_order_of_precedence(self):
        self.assertEqual(insights.categorize(self.facts(refutation={"ends_in_mate": True}))[0], "mate")
        fork = {"kind": "fork", "by": "black", "index": 0, "gain": 0}
        self.assertEqual(insights.categorize(self.facts(refutation={"tactics": [fork]})), ("tactic", "fork"))
        hang = {"kind": "hangs", "gain": -3}
        self.assertEqual(insights.categorize(self.facts(played_tactics=[hang]))[0], "hung")
        threat = {"move": "Qxf7#", "addressed": False}
        self.assertEqual(insights.categorize(self.facts(threat=threat))[0], "threat")

    def test_king_material_and_fallbacks(self):
        self.assertEqual(insights.categorize(self.facts(refutation={"san": ["Qh5+", "Kd7"]}))[0], "king")
        self.assertEqual(insights.categorize(self.facts(refutation={"material_change": -1}))[0], "material")
        self.assertEqual(insights.categorize(self.facts(move={"color": "white", "win_chance_before": 85}))[0], "slip")
        self.assertEqual(insights.categorize(self.facts())[0], "positional")

    def test_opening_family(self):
        self.assertEqual(insights.opening_family("Caro Kann Defense Advance Botvinnik Carls Variation"),
                         "Caro Kann Defense")
        self.assertEqual(insights.opening_family("Italian Game Two Knights Defense"), "Italian Game")
        self.assertEqual(insights.opening_family(""), "Unknown")


class ProfileTests(unittest.TestCase):
    def setUp(self):
        insights._cache.clear()
        _, evals, _ = scholars_review()
        self.evals = evals

    def test_extract_finds_the_blunder_and_makes_a_puzzle(self):
        ex = insights.extract_game(fake_game(), self.evals)
        self.assertEqual([m["san"] for m in ex["mistakes"]], ["Nf6"])
        self.assertEqual(ex["mistakes"][0]["category"], "mate")
        self.assertEqual(len(ex["puzzles"]), 1)
        self.assertEqual(ex["puzzles"][0]["solution_san"], ["Qe7"])
        self.assertEqual(ex["first_mistake"], 6)

    def test_profile_aggregates(self):
        games = [fake_game("g1", end=1), fake_game("g2", color="white", result="Win", end=2),
                 fake_game("g3", end=3)]
        ex = [insights.extract_game(games[0], self.evals)]
        p = insights.build_profile(games, ex)
        self.assertEqual((p["games"], p["analysed"], p["mistakes"]), (3, 1, 1))
        self.assertEqual(p["categories"][0]["key"], "mate")
        self.assertEqual(p["wins"], 1)
        italian_black = next(o for o in p["openings"] if o["color"] == "black")
        self.assertEqual((italian_black["games"], italian_black["score"]), (2, 0.0))

    def test_headlines_need_enough_data(self):
        p = insights.build_profile([fake_game()], [insights.extract_game(fake_game(), self.evals)])
        self.assertEqual(p["headlines"], [])  # one mistake is not a pattern


class SpacedRepetitionTests(unittest.TestCase):
    def test_intervals_grow_when_solved(self):
        card, due = srs.review(srs.Card(), True, now=0)
        self.assertEqual((card.interval_d, due), (1.0, srs.DAY))
        card, _ = srs.review(card, True, now=0)
        self.assertEqual(card.interval_d, 3.0)
        card, _ = srs.review(card, True, now=0)
        self.assertGreater(card.interval_d, 7)

    def test_miss_resets_and_lowers_ease(self):
        card, due = srs.review(srs.Card(interval_d=10, ease=2.5, reps=4), False, now=100)
        self.assertEqual((card.reps, card.lapses, due), (0, 1, 100 + srs.RETRY_SECONDS))
        self.assertLess(card.ease, 2.5)
        worst = srs.Card(ease=srs.MIN_EASE)
        self.assertEqual(srs.review(worst, False, now=0)[0].ease, srs.MIN_EASE)


if __name__ == "__main__":
    unittest.main()
