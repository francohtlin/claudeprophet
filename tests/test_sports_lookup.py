from __future__ import annotations

import unittest

from sports_lookup.lookup import infer_sports, meaningful_terms, normalize_espn_event


class SportsLookupTests(unittest.TestCase):
    def test_infers_sports_from_query(self) -> None:
        self.assertEqual(infer_sports("Will the Lakers win?", "auto"), ["nba"])
        self.assertEqual(infer_sports("anything", "nhl"), ["nhl"])

    def test_meaningful_terms_removes_stop_words(self) -> None:
        self.assertIn("lakers", meaningful_terms("Will the Lakers win?"))
        self.assertNotIn("will", meaningful_terms("Will the Lakers win?"))

    def test_normalizes_espn_event(self) -> None:
        event = {
            "name": "Away at Home",
            "shortName": "AWY @ HOM",
            "date": "2026-01-01T00:00Z",
            "status": {"type": {"description": "Scheduled", "completed": False}},
            "competitions": [
                {
                    "venue": {"fullName": "Arena"},
                    "competitors": [
                        {"homeAway": "home", "score": "0", "team": {"displayName": "Home", "abbreviation": "HOM"}}
                    ],
                }
            ],
        }
        normalized = normalize_espn_event(event, "nba")
        self.assertEqual(normalized["event"], "Away at Home")
        self.assertEqual(normalized["competitors"][0]["team"], "Home")


if __name__ == "__main__":
    unittest.main()
