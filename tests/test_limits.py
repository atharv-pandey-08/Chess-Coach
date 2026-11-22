import unittest

from chess_coach.limits import DailyLimiter


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class LimiterTest(unittest.TestCase):
    def test_per_client_cap(self):
        lim = DailyLimiter(per_client=2, total=0, clock=Clock())
        self.assertIsNone(lim.take("a"))
        self.assertIsNone(lim.take("a"))
        self.assertEqual(lim.take("a"), "client")
        self.assertIsNone(lim.take("b"))  # other visitors are unaffected
        self.assertEqual(lim.remaining("a"), 0)

    def test_server_cap_applies_to_everyone(self):
        lim = DailyLimiter(per_client=10, total=2, clock=Clock())
        lim.take("a")
        lim.take("b")
        self.assertEqual(lim.take("c"), "server")
        self.assertEqual(lim.remaining("c"), 0)

    def test_resets_next_day(self):
        clock = Clock()
        lim = DailyLimiter(per_client=1, total=0, clock=clock)
        lim.take("a")
        self.assertEqual(lim.take("a"), "client")
        clock.t += 86_400
        self.assertIsNone(lim.take("a"))

    def test_no_cap(self):
        lim = DailyLimiter(per_client=0, total=0)
        for _ in range(100):
            self.assertIsNone(lim.take("a"))
        self.assertIsNone(lim.remaining("a"))


if __name__ == "__main__":
    unittest.main()
