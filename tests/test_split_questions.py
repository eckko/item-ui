#!/usr/bin/env python3
"""Tests for tools/site/split_questions.py and its use in build_pool.py.
Run: python3 tests/test_split_questions.py"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "site"))
sys.path.insert(0, str(ROOT / "tools"))

import check_site  # noqa: E402
import opaque  # noqa: E402
import split_questions as sq  # noqa: E402
from test_opaque import KEY, OpaqueCase  # noqa: E402


def bank(units, per_unit=2, extra=None):
    questions = []
    for u in units:
        for n in range(per_unit):
            questions.append({"id": "%s-%d" % (u, n), "unit": u, "q": "?"})
    out = {"id": "b", "book": "B", "labels": {"unit": "Chapter"},
           "questions": questions}
    out.update(extra or {})
    return out


class SplitTests(unittest.TestCase):
    def test_one_chunk_means_no_split(self):
        self.assertEqual(sq.chunks(bank(["Chapter 1"])), [])
        self.assertEqual(sq.chunks({"questions": []}), [])

    def test_one_chunk_per_unit_in_book_order(self):
        parts = sq.chunks(bank(["Chapter 2", "Chapter 1", "Chapter 10"]))
        self.assertEqual([p for p, _ in parts], [
            "quiz/q/chapter-2.enc", "quiz/q/chapter-1.enc",
            "quiz/q/chapter-10.enc"])

    def test_first_chunk_carries_the_book_keys_only(self):
        parts = sq.chunks(bank(["A", "B"]))
        self.assertEqual(parts[0][1]["id"], "b")
        self.assertEqual(parts[0][1]["labels"], {"unit": "Chapter"})
        self.assertEqual(list(parts[1][1]), ["questions"])

    def test_merge_gives_back_the_bank(self):
        original = bank(["A", "B", "C"], 3)
        merged = sq.merge([b for _, b in sq.chunks(original)])
        self.assertEqual(merged, original)

    def test_interleaved_units_are_grouped(self):
        original = bank(["A", "B"], 1)
        original["questions"].append({"id": "a2", "unit": "A"})
        merged = sq.merge([b for _, b in sq.chunks(original)])
        self.assertEqual([q["id"] for q in merged["questions"]],
                         ["A-0", "a2", "B-0"])

    def test_big_unit_is_cut_and_names_stay_unique(self):
        parts = sq.chunks(bank(["A"], 7), max_per_chunk=3)
        self.assertEqual([p for p, _ in parts], [
            "quiz/q/a-1.enc", "quiz/q/a-2.enc", "quiz/q/a-3.enc"])
        self.assertEqual([len(b["questions"]) for _, b in parts], [3, 3, 1])

    def test_colliding_slugs_get_a_suffix(self):
        parts = sq.chunks(bank(["Ch. 1", "Ch 1", "ch-1"], 1))
        names = [p for p, _ in parts]
        self.assertEqual(len(set(names)), 3)

    def test_missing_unit_has_a_chunk(self):
        original = bank(["A"], 1)
        original["questions"].append({"id": "x"})
        names = [p for p, _ in sq.chunks(original)]
        self.assertIn("quiz/q/unsorted.enc", names)

    def test_name_does_not_move_when_an_earlier_unit_changes(self):
        base = dict(sq.chunks(bank(["A", "B", "C"])))
        more = dict(sq.chunks(bank(["A", "B", "C"], 5)))
        self.assertEqual(set(base), set(more))
        shifted = dict(sq.chunks(bank(["X", "A", "B", "C"])))
        self.assertTrue(set(base) <= set(shifted))


class PoolTests(OpaqueCase):
    def put_questions(self, data):
        (self.book / "quiz" / "questions.enc").write_bytes(
            opaque.seal(KEY, json.dumps(data).encode(), True))

    def entry(self):
        self.run_build()
        return opaque.read_index(self.site, KEY)["books"][0]

    def test_small_bank_stays_one_file(self):
        self.put_questions(bank(["A"]))
        entry = self.entry()
        self.assertIn("quiz/questions.enc", entry["files"])
        self.assertNotIn("questionParts", entry)

    def test_big_bank_is_split_and_listed(self):
        self.put_questions(bank(["A", "B", "C"]))
        entry = self.entry()
        self.assertNotIn("quiz/questions.enc", entry["files"])
        self.assertEqual(entry["questionParts"], [
            "quiz/q/a.enc", "quiz/q/b.enc", "quiz/q/c.enc"])
        for path in entry["questionParts"]:
            self.assertIn(path, entry["files"])

    def test_unchanged_chapters_keep_their_pool_files(self):
        self.put_questions(bank(["A", "B", "C"]))
        first = self.entry()
        changed = bank(["A", "B", "C"])
        changed["questions"][-1]["q"] = "edited"
        self.put_questions(changed)
        second = self.entry()
        for same in ("quiz/q/a.enc", "quiz/q/b.enc"):
            self.assertEqual(first["files"][same], second["files"][same])
        self.assertNotEqual(first["files"]["quiz/q/c.enc"],
                            second["files"]["quiz/q/c.enc"])

    def test_check_site_accepts_a_split_site(self):
        self.put_questions(bank(["A", "B"]))
        self.run_build()
        (self.site / "subject.json").unlink(missing_ok=True)
        (self.site / "index.html").write_text("<html><body></body></html>")
        self.assertEqual(
            check_site.check_with_key(self.site, KEY, [], ""), [])

    def test_real_loader_returns_the_whole_bank(self):
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        original = bank(["A", "B", "C"], 3)
        self.put_questions(original)
        entry = self.entry()
        out = subprocess.run(
            ["node", str(ROOT / "tests" / "questions-e2e.js"),
             str(self.site), KEY.hex(), entry["id"]],
            capture_output=True, text=True, check=True)
        found = json.loads(out.stdout)
        self.assertEqual(found["bank"], original)
        self.assertEqual(found["requests"], 4)   # index + 3 chunks

    def test_real_loader_reads_the_single_file_too(self):
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        original = bank(["A"])
        self.put_questions(original)
        entry = self.entry()
        out = subprocess.run(
            ["node", str(ROOT / "tests" / "questions-e2e.js"),
             str(self.site), KEY.hex(), entry["id"]],
            capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(out.stdout)["bank"], original)


if __name__ == "__main__":
    unittest.main()
