#!/usr/bin/env python3
"""Tests for tools/site/publish_r2.py with an in-memory S3 stand-in.
Run: python3 tests/test_publish_r2.py"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "site"))
sys.path.insert(0, str(ROOT / "tools"))

import opaque  # noqa: E402
import publish_r2  # noqa: E402

KEY = bytes(range(32))
BUCKET = "books"
ID = "ab" * 16


class NotFound(Exception):
    """What boto3 raises for a missing object."""

    response = {"Error": {"Code": "404"}}


class FakeBody:
    """A response body with read()."""

    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data


class FakeS3:
    """The few S3 calls publish_r2 makes, on a dict."""

    def __init__(self):
        self.store = {}
        self.puts = []

    def head_object(self, Bucket, Key):
        if Key not in self.store:
            raise NotFound()
        return {"Metadata": self.store[Key][1]}

    def get_object(self, Bucket, Key):
        if Key not in self.store:
            raise NotFound()
        return {"Body": FakeBody(self.store[Key][0])}

    def put_object(self, Bucket, Key, Body, ContentType, Metadata):
        self.store[Key] = (Body, Metadata)
        self.puts.append(Key)

    def delete_object(self, Bucket, Key):
        del self.store[Key]

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.store if k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in keys],
                "IsTruncated": False}


def seal(data, compress=True):
    """One RQE1 file."""
    return opaque.seal(KEY, data, compress)


def make_built(folder, questions=None, chapter=b'{"nodes": []}'):
    """A built book folder (what build_book.py writes)."""
    for part in ("data/ch", "media", "quiz"):
        (folder / part).mkdir(parents=True, exist_ok=True)
    (folder / "book.json").write_text(json.dumps(
        {"id": "alpha", "title": "Alpha", "views": {"read": 1, "quiz": 2}}))
    manifest = {"format": 1, "book": {"id": "alpha", "title": "Alpha",
                                      "summary": "s", "cover": None}}
    (folder / "data/manifest.enc").write_bytes(
        seal(json.dumps(manifest).encode()))
    (folder / "data/ch/c01.enc").write_bytes(seal(chapter))
    (folder / "media/pic.enc").write_bytes(seal(b"\x89PNG", False))
    bank = {"id": "alpha", "book": "Alpha",
            "questions": questions or [{"id": "q1", "unit": "u1"}]}
    (folder / "quiz/questions.enc").write_bytes(
        seal(json.dumps(bank).encode()))


class PublishTests(unittest.TestCase):
    """The behaviour the plan asks for in step 2.2."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.built = Path(self.tmp.name) / "built"
        self.book = Path(self.tmp.name) / "book"
        self.book.mkdir()
        make_built(self.built)
        self.s3 = FakeS3()

    def run_publish(self, **kwargs):
        """Publish with the fixture; returns the counts."""
        return publish_r2.publish(self.s3, BUCKET, self.built, self.book,
                                  KEY, ID, **kwargs)

    def test_first_run_uploads_everything_card_last(self):
        counts = self.run_publish(tags=["habit"])
        self.assertEqual(counts["uploaded"], counts["files"])
        self.assertEqual(self.s3.puts[-1], "p/%s/card.enc" % ID)
        self.assertLess(self.s3.puts.index("p/%s/data/ch/c01.enc" % ID),
                        self.s3.puts.index("p/%s/data/manifest.enc" % ID))
        for body, _ in self.s3.store.values():
            self.assertEqual(body[:4], b"RQE1")

    def test_card_holds_counts_and_decrypts(self):
        self.run_publish(tags=["habit"], author="A. Writer")
        card = json.loads(opaque.unseal(
            KEY, self.s3.store["p/%s/card.enc" % ID][0]))
        self.assertEqual((card["t"], card["a"], card["tags"]),
                         ("Alpha", "A. Writer", ["habit"]))
        self.assertEqual((card["ch"], card["q"]), (1, 1))
        self.assertEqual(card["views"], ["quiz", "read"])

    def test_second_run_uploads_nothing(self):
        self.run_publish()
        self.s3.puts.clear()
        counts = self.run_publish()
        self.assertEqual(counts["uploaded"], 0)
        self.assertEqual(self.s3.puts, [])

    def test_changed_chapter_uploads_chapter_and_card(self):
        self.run_publish()
        self.s3.puts.clear()
        (self.built / "data/ch/c01.enc").write_bytes(seal(b'{"nodes":[1]}'))
        self.run_publish()
        self.assertEqual(sorted(self.s3.puts), sorted([
            "p/%s/data/ch/c01.enc" % ID, "p/%s/card.enc" % ID]))

    def test_added_time_is_kept(self):
        self.run_publish()
        first = opaque.unseal(KEY, self.s3.store["p/%s/card.enc" % ID][0])
        self.run_publish()
        again = opaque.unseal(KEY, self.s3.store["p/%s/card.enc" % ID][0])
        self.assertEqual(json.loads(first)["added"],
                         json.loads(again)["added"])

    def test_orphan_is_removed(self):
        self.run_publish()
        self.s3.store["p/%s/data/ch/old.enc" % ID] = (b"x", {})
        counts = self.run_publish()
        self.assertEqual(counts["deleted"], 1)
        self.assertNotIn("p/%s/data/ch/old.enc" % ID, self.s3.store)

    def test_other_books_are_untouched(self):
        other = "p/%s/card.enc" % ("cd" * 16)
        self.s3.store[other] = (b"x", {})
        self.run_publish()
        self.assertIn(other, self.s3.store)

    def test_dry_run_writes_nothing(self):
        counts = self.run_publish(dry_run=True)
        self.assertGreater(counts["uploaded"], 0)
        self.assertEqual(self.s3.store, {})

    def test_plain_file_is_refused(self):
        (self.built / "data/ch/plain.txt").write_text("hello")
        with self.assertRaises(publish_r2.PublishError):
            self.run_publish()
        self.assertEqual(self.s3.store, {})

    def test_wrong_key_or_wrong_magic_is_refused(self):
        (self.built / "data/ch/c01.enc").write_bytes(b"NOPE" + b"0" * 60)
        with self.assertRaises(publish_r2.PublishError):
            self.run_publish()
        self.assertEqual(self.s3.store, {})

    def test_big_bank_is_split_and_listed(self):
        bank = [{"id": "q%d" % i, "unit": "u%d" % (i % 2)}
                for i in range(6)]
        make_built(self.built, questions=bank)
        self.run_publish()
        parts = json.loads(opaque.unseal(
            KEY, self.s3.store["p/%s/quiz/parts.enc" % ID][0]))
        self.assertEqual(parts, ["quiz/q/u0.enc", "quiz/q/u1.enc"])
        self.assertNotIn("p/%s/quiz/questions.enc" % ID, self.s3.store)

    def test_book_id_file(self):
        with self.assertRaises(publish_r2.PublishError):
            publish_r2.book_id_of(self.book, True)
        made, created = publish_r2.book_id_of(self.book, False)
        self.assertTrue(created and len(made) == 32)
        self.assertEqual(publish_r2.book_id_of(self.book, True),
                         (made, False))

    def test_keys_name_nothing(self):
        self.run_publish(tags=["habit"])
        for key in self.s3.store:
            self.assertRegex(key, r"^p/[0-9a-f]{32}/(card|cover)\.enc$|"
                             r"^p/[0-9a-f]{32}/(data|media|quiz)/")


if __name__ == "__main__":
    unittest.main()
