"""Stats, sample shape, and HTML escaping.

    python3 -m unittest tests.test_chatstats
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chatstats  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def msg(**overrides) -> dict:
    base = {
        "date": "2025-01-01T12:00:00",
        "sender": "Me",
        "body": "hello there friend",
        "quote": "",
        "sticker": None,
        "reactions": [],
        "attachments": [],
        "deleted": False,
        "call": False,
        "missed": False,
    }
    base.update(overrides)
    return chatstats.normalize(base)


class TestParse(unittest.TestCase):
    def test_ndjson_skips_a_bad_line(self):
        text = '{"date":"2025-01-01T00:00:00","sender":"Me","body":"hi"}\nNOT JSON\n'
        messages, errors = chatstats.parse_text(text)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["body"], "hi")
        self.assertEqual(len(errors), 1)

    def test_json_array_and_reaction_shapes(self):
        payload = [
            {
                "date": "2025-06-02T15:04:00Z",
                "sender": "Me",
                "body": "hi  ",
                "reactions": [["Aya", "❤️"], {"name": "Jim", "emoji": "👍"}],
                "sticker": "🍍",
            }
        ]
        messages, errors = chatstats.parse_text(json.dumps(payload))
        self.assertEqual(errors, [])
        self.assertEqual(messages[0]["body"], "hi")
        self.assertEqual(messages[0]["date"], datetime(2025, 6, 2, 15, 4))
        self.assertEqual(messages[0]["reactions"], [("Aya", "❤️"), ("Jim", "👍")])
        self.assertEqual(messages[0]["sticker"]["emoji"], "🍍")

    def test_export_roundtrip_strips_sigexport_padding(self):
        original = chatstats.generate_messages(n=5, seed=1)[0]
        exported = chatstats.to_export(original)
        self.assertEqual(list(exported), list(chatstats.FIELDS))
        self.assertTrue(exported["body"].endswith("  "))
        loaded, errors = chatstats.parse_text(json.dumps(exported) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(loaded[0]["body"], original["body"])
        self.assertEqual(loaded[0]["sender"], "Me")


class TestAnalyze(unittest.TestCase):
    def test_sessions_replies_quotes_and_reactions(self):
        base = datetime(2025, 1, 1, 12, 0)
        messages = [
            msg(date=base.isoformat(), sender="Me", body="hello there friend"),
            msg(
                date=(base + timedelta(minutes=5)).isoformat(),
                sender="Aya",
                body="hi back",
                quote="\n\n> hello there friend\n\n",
                reactions=[["Me", "❤️"]],
            ),
            msg(date=(base + timedelta(hours=3)).isoformat(), sender="Me", body="after the gap"),
        ]
        stats = chatstats.analyze(messages, gap_minutes=30)
        self.assertEqual(stats["sessions"]["count"], 2)
        self.assertEqual(stats["latency"]["count"], 1)
        self.assertEqual(stats["latency"]["median"], 300)
        self.assertEqual(stats["quotes"]["matched"], 1)
        self.assertEqual(stats["quotes"]["targets"]["Me"], 1)
        self.assertEqual(stats["senders"][0]["reactions_given"] + stats["senders"][1]["reactions_given"], 1)
        aya = next(sender for sender in stats["senders"] if sender["name"] == "Aya")
        self.assertEqual(aya["reactions_received"], 1)

    def test_double_text_is_not_a_reply(self):
        messages = [
            msg(date="2025-01-01T12:00:00", body="one one"),
            msg(date="2025-01-01T12:01:00", body="two two"),
        ]
        stats = chatstats.analyze(messages)
        me = stats["senders"][0]
        self.assertEqual(me["double_texts"], 1)
        self.assertEqual(stats["latency"]["count"], 0)

    def test_calls_and_deletes_do_not_count_as_words(self):
        messages = [
            msg(body="Outgoing voice call (accepted, 3m 12s)", call=True, sender="Me"),
            msg(body="secret words here", deleted=True, sender="Aya"),
            msg(body="real words", sender="Jim"),
        ]
        stats = chatstats.analyze(messages)
        self.assertEqual(stats["overview"]["words"], 2)
        self.assertEqual(stats["overview"]["deleted"], 1)
        self.assertEqual(stats["calls"]["seconds"], 192)
        self.assertEqual(stats["calls"]["parsed"], 1)

    def test_daily_series_keeps_the_hole(self):
        messages = [
            msg(date="2025-01-01T12:00:00"),
            msg(date="2025-01-03T12:00:00", sender="Aya", body="later"),
        ]
        stats = chatstats.analyze(messages)
        self.assertEqual([point["count"] for point in stats["daily"]["points"]], [1, 0, 1])
        self.assertEqual(sum(sum(row) for row in stats["heatmap"]), 2)

    def test_empty_file(self):
        stats = chatstats.analyze([])
        html_out = chatstats.render_html(stats, title="empty", source="empty.ndjson")
        self.assertIn("No messages", html_out)
        self.assertNotIn("<script>", html_out)

    def test_html_escapes_names_and_bodies(self):
        hostile = msg(
            sender="<b>Eve</b>",
            body="<script>alert(1)</script> hello there",
            reactions=[["<img>", "🔥"]],
        )
        html_out = chatstats.render_html(chatstats.analyze([hostile]), title="t", source="x")
        self.assertNotIn("<script>", html_out)
        self.assertNotIn("<b>Eve</b>", html_out)
        self.assertNotIn("<img>", html_out)
        self.assertIn("&lt;b&gt;Eve&lt;/b&gt;", html_out)
        self.assertIn("&lt;script&gt;", html_out)


class TestSample(unittest.TestCase):
    def test_opening_matches_the_exporter_example(self):
        messages = chatstats.generate_messages(n=10, seed=1)
        first = chatstats.to_export(messages[0])
        self.assertEqual(first["sender"], "Me")
        self.assertEqual(first["body"], "How is everyone?  ")
        self.assertEqual(first["date"], "2025-06-02T15:04:00")
        self.assertIsNone(first["sticker"])
        self.assertEqual(chatstats.to_export(messages[1])["reactions"], [["Me", "❤️"]])
        self.assertEqual(messages[2]["body"], "I'm not.")

    def test_sample_covers_every_schema_edge(self):
        messages = chatstats.generate_messages()
        self.assertGreaterEqual(len(messages), 900)
        self.assertLess(messages[0]["date"], messages[-1]["date"])
        self.assertTrue(any(item["sticker"] and item["sticker"].get("extension") for item in messages))
        self.assertTrue(any(item["sticker"] and item["sticker"].get("extension") is None for item in messages))
        self.assertTrue(any(item["deleted"] for item in messages))
        self.assertTrue(any(item["call"] and not item["missed"] for item in messages))
        self.assertTrue(any(item["call"] and item["missed"] for item in messages))
        self.assertTrue(any(chatstats.norm_text(item["quote"]) == "How is everyone?" for item in messages))
        self.assertTrue(any("%20" in attachment["path"] for item in messages for attachment in item["attachments"]))
        self.assertTrue(any(attachment.get("missing_kind") for item in messages for attachment in item["attachments"]))
        july = sum(1 for item in messages if item["date"].month == 7 and item["date"].year == 2025)
        august = sum(1 for item in messages if item["date"].month == 8 and item["date"].year == 2025)
        self.assertGreater(july, 0)
        self.assertLess(august, july * 0.75)
        stats = chatstats.analyze(messages)
        self.assertEqual(sum(point["count"] for point in stats["daily"]["points"]), stats["overview"]["messages"])
        self.assertGreaterEqual(stats["quotes"]["matched"], 1)
        self.assertIn("Me", stats["quotes"]["targets"])

    def test_examples_are_the_readable_tour(self):
        picks = chatstats.pick_examples(chatstats.generate_messages())
        blob = json.dumps(picks)
        self.assertIn("How is everyone?", blob)
        self.assertIn("trip%20photo.jpg", blob)
        self.assertIn("missing_kind", blob)
        self.assertGreaterEqual(len(picks), 8)

    def test_committed_files_match_the_generator(self):
        messages = chatstats.generate_messages()
        ndjson = "".join(json.dumps(chatstats.to_export(item), ensure_ascii=False) + "\n" for item in messages)
        examples = json.dumps(chatstats.pick_examples(messages), ensure_ascii=False, indent=2) + "\n"
        self.assertEqual((ROOT / "testdata" / "sample.ndjson").read_text(encoding="utf-8"), ndjson)
        self.assertEqual((ROOT / "testdata" / "examples.json").read_text(encoding="utf-8"), examples)


class TestCli(unittest.TestCase):
    def test_sample_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sample = folder / "chat.ndjson"
            report = folder / "chat.html"
            self.assertEqual(chatstats.main(["sample", "-o", str(sample), "--messages", "80", "--seed", "2"]), 0)
            self.assertEqual(chatstats.main(["report", str(sample), "-o", str(report)]), 0)
            text = report.read_text(encoding="utf-8")
            self.assertIn('id="overview"', text)
            self.assertIn('id="language"', text)
            self.assertIn("How is everyone?", text)
            self.assertIn("Aya", text)
            self.assertIn("Print this page to save a PDF.", text)


if __name__ == "__main__":
    unittest.main()
