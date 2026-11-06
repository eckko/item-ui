#!/usr/bin/env python3
"""Tests for plain book media: tools/plain_media.py, media_types.py and the
media step of tools/book_build/build_book.py.
Run: python3 tests/test_plain_media.py"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "book_build"))

import encrypt_media  # noqa: E402
import media_types  # noqa: E402
import plain_media  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 20


def bank(*entries):
    return {"id": "t1", "book": "T", "questions": [
        {"id": "q1", "unit": "Chapter 1", "section": "A", "kind": "tf",
         "q": "x", "answer": True, "media": list(entries)}]}


def old_file(book, file_id, data):
    path = Path(book) / "media" / (file_id + ".enc")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt_media.encrypt(plain_media.STAND_IN_KEY, file_id, data))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.book = self.tmp / "book"
        self.book.mkdir()

    def write_bank(self, data):
        (self.book / "questions.json").write_text(json.dumps(data))


class TableTests(unittest.TestCase):
    def test_python_and_browser_tables_agree(self):
        text = (ROOT / "js" / "media-store.js").read_text()
        block = re.search(r"const EXTENSIONS = \{(.*?)\};", text, re.S).group(1)
        js = dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', block))
        self.assertEqual(js, media_types.EXTENSIONS)

    def test_default_type_is_webp(self):
        self.assertEqual(media_types.extension_of(None), "webp")
        self.assertEqual(media_types.extension_of("image/png"), "png")
        self.assertIsNone(media_types.extension_of("text/html"))

    def test_entries_are_found_everywhere(self):
        data = {"cover": {"id": "c"}, "cards": [{"media": [{"id": "a"}],
                "blocks": [{"media": [{"id": "b"}, {"src": "x.png"}]}]}]}
        self.assertEqual(sorted(e["id"] for e in media_types.entries(data)),
                         ["a", "b", "c"])


class ConvertTests(Base):
    def test_type_from_the_entry_else_from_the_bytes(self):
        self.write_bank(bank({"id": "u1/a", "type": "image/png"}))
        old_file(self.book, "u1/a", PNG)
        old_file(self.book, "u1/b", WEBP)       # no entry uses it
        done = plain_media.convert(self.book)
        self.assertEqual(sorted(new for _, new in done),
                         ["media/u1/a.png", "media/u1/b.webp"])
        self.assertEqual((self.book / "media/u1/a.png").read_bytes(), PNG)
        self.assertEqual(list((self.book / "media").rglob("*.enc")), [])

    def test_dry_run_changes_nothing(self):
        self.write_bank(bank())
        old_file(self.book, "u1/a", PNG)
        plain_media.convert(self.book, dry_run=True)
        self.assertTrue((self.book / "media/u1/a.enc").exists())
        self.assertFalse((self.book / "media/u1/a.png").exists())

    def test_a_book_with_its_own_key_stops_everything(self):
        self.write_bank(bank())
        old_file(self.book, "u1/a", PNG)
        other = self.book / "media/u1/b.enc"
        other.write_bytes(encrypt_media.encrypt(b"k" * 32, "u1/b", PNG))
        with self.assertRaises(SystemExit) as raised:
            plain_media.convert(self.book)
        self.assertIn("another key", str(raised.exception))
        self.assertTrue((self.book / "media/u1/a.enc").exists())  # untouched

    def test_unknown_bytes_without_a_type_stop(self):
        self.write_bank(bank())
        old_file(self.book, "u1/a", b"???? not a picture")
        with self.assertRaises(SystemExit):
            plain_media.convert(self.book)

    def test_nothing_to_do(self):
        self.assertEqual(plain_media.convert(self.book), [])


class BuildTests(Base):
    def build(self, data):
        self.write_bank(data)
        key = base64.b64encode(os.urandom(32)).decode()
        out = self.tmp / "out"
        result = subprocess.run(
            [sys.executable, str(TOOLS / "book_build" / "build_book.py"),
             "--book", str(self.book), "--template-tag", "3.0.0",
             "--out", str(out)], capture_output=True, text=True,
            env={**os.environ, "SUBJECT_KEY": key})
        return result, out, base64.b64decode(key)

    def put(self, name, data):
        path = self.book / "media" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_plain_files_are_encrypted_with_the_subject_key(self):
        self.put("u1/a.png", PNG)
        self.put("u1/b.webp", WEBP)
        result, out, key = self.build(bank(
            {"id": "u1/a", "type": "image/png"}, {"id": "u1/b"}))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        stored = out / "quiz" / "media" / "u1" / "a.enc"
        self.assertEqual(encrypt_media.decrypt(key, "u1/a", stored.read_bytes()), PNG)
        self.assertTrue((out / "quiz/media/u1/b.enc").exists())

    def test_old_encrypted_media_stops_the_build(self):
        old_file(self.book, "u1/a", PNG)
        result, _, _ = self.build(bank({"id": "u1/a"}))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plain_media.py", result.stdout + result.stderr)

    def test_wrong_extension_for_the_type_stops_the_build(self):
        self.put("u1/a.png", PNG)
        result, _, _ = self.build(bank({"id": "u1/a"}))   # type means webp
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rename the file or fix the type", result.stdout + result.stderr)

    def test_unsupported_type_stops_the_build(self):
        result, _, _ = self.build(bank({"id": "u1/a", "type": "text/html"}))
        self.assertNotEqual(result.returncode, 0)

    def test_missing_file_is_a_warning_and_other_files_are_skipped(self):
        self.put("notes.txt", b"hello")
        result, out, _ = self.build(bank({"id": "u1/a"}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no file media/u1/a.webp", result.stdout)
        self.assertIn("skipped media/notes.txt", result.stdout)

    def test_two_files_for_one_id_stop_the_build(self):
        self.put("u1/a.png", PNG)
        self.put("u1/a.webp", WEBP)
        result, _, _ = self.build(bank({"id": "u1/a"}))
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
