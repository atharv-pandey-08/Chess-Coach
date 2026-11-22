import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chess_coach import config, storage
from scripts.make_seed_db import build


class SeedTest(unittest.TestCase):
    def test_seed_keeps_one_player_and_copies_on_first_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, seed, live = Path(tmp, "src.db"), Path(tmp, "seed.db"), Path(tmp, "data", "live.db")
            with mock.patch.object(config, "DB_PATH", src):
                storage.init()
                storage.save_games("Me", [{"id": "1", "end_time": 1}])
                storage.save_games("Other", [{"id": "2", "end_time": 2}])
                storage.save_puzzles("me", [{"id": "1:5"}])
            counts = build(src, seed, "Me")
            self.assertEqual(counts["games"], 1)
            with mock.patch.object(config, "DB_PATH", live), mock.patch.object(config, "SEED_DB", seed):
                storage.init()
                with sqlite3.connect(live) as c:
                    self.assertEqual(c.execute("SELECT id FROM games").fetchall(), [("1",)])
                    self.assertEqual(c.execute("SELECT due FROM puzzles").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
