#!/usr/bin/env python3
"""Tests for tools/site/opaque.py and build_pool.py.
Run: python3 tests/test_opaque.py"""

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "site"))
sys.path.insert(0, str(ROOT / "tools"))

import build_pool  # noqa: E402
import check_site  # noqa: E402
import encrypt_media  # noqa: E402
import opaque  # noqa: E402

KEY = bytes(range(32))


def make_book(folder, book_id="alpha", title="Alpha Title"):
    """A built book folder in the layout the book build writes."""
    for part in ("data/ch", "media", "quiz/media"):
        (folder / part).mkdir(parents=True, exist_ok=True)
    (folder / "book.json").write_text(json.dumps(
        {"id": book_id, "title": title,
         "views": {"read": "x", "quiz": "y"}}))
    manifest = {"format": 1, "book": {
        "id": book_id, "title": title, "summary": "About it",
        "cover": book_id + "/front/cover"}}
    put = lambda name, data, z: (folder / name).write_bytes(
        opaque.seal(KEY, data, z))
    put("data/manifest.enc", json.dumps(manifest).encode(), True)
    put("data/ch/c01.enc", b'{"nodes": []}', True)
    put("media/" + book_id + "_front_cover.enc", b"\x89PNG-cover", False)
    put("quiz/questions.enc", b'{"questions": []}', True)
    # a picture in the older layout (nonce first, no header)
    (folder / "quiz" / "media" / "u01.enc").write_bytes(
        encrypt_media.encrypt(KEY, "u01", b"\x89PNG-old"))


class OpaqueCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.site = self.tmp / "site"
        self.site.mkdir()
        (self.site / "subject.json").write_text(json.dumps(
            {"title": "My Subject", "templateTag": "3.4.0",
             "uiVersion": "2", "theme": {"name": "plain"}}))
        self.keyfile = self.tmp / "key"
        self.keyfile.write_text(KEY.hex())
        self.book = self.tmp / "alpha"
        make_book(self.book)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_build(self, *extra):
        return build_pool.main(["--key", str(self.keyfile), "--site",
                                str(self.site), *extra, str(self.book)])

    def pool_files(self):
        return sorted(p.relative_to(self.site).as_posix()
                      for p in self.site.glob("bin/??/*.bin"))


class MoveIntoBinTests(OpaqueCase):
    def old_layout(self):
        """Rebuild the site, then put it back as the old layout was."""
        self.run_build()
        index = opaque.read_index(self.site, KEY)
        for book in index["books"]:
            book["files"] = {k: v[len("bin/"):]
                             for k, v in book["files"].items()}
        opaque.write_index(self.site, KEY, index)
        for path in (self.site / "bin").iterdir():
            path.rename(self.site / path.name)
        (self.site / "bin").rmdir()

    def test_old_pool_is_moved_and_index_repointed(self):
        self.old_layout()
        count = len(opaque.old_pool_files(self.site))
        self.assertGreater(count, 0)
        self.assertEqual(opaque.move_pool_into_bin(self.site, KEY), count)
        self.assertEqual(opaque.old_pool_files(self.site), [])
        index = opaque.read_index(self.site, KEY)
        for ref in index["books"][0]["files"].values():
            self.assertRegex(ref, opaque.REF_PATTERN)
            self.assertTrue((self.site / ref).is_file())

    def test_moving_twice_does_nothing(self):
        self.old_layout()
        opaque.move_pool_into_bin(self.site, KEY)
        self.assertEqual(opaque.move_pool_into_bin(self.site, KEY), 0)

    def test_build_after_old_layout_keeps_every_file(self):
        self.old_layout()
        self.run_build()
        self.assertEqual(opaque.old_pool_files(self.site), [])
        self.assertGreater(len(self.pool_files()), 0)

    def test_wrong_key_moves_nothing(self):
        self.old_layout()
        before = opaque.old_pool_files(self.site)
        with self.assertRaises(Exception):
            opaque.move_pool_into_bin(self.site, bytes(32))
        self.assertEqual(opaque.old_pool_files(self.site), before)


class IdTests(OpaqueCase):
    def test_subkey_differs_from_key_and_by_purpose(self):
        self.assertNotEqual(opaque.subkey(KEY, "names"), KEY)
        self.assertNotEqual(opaque.subkey(KEY, "names"),
                            opaque.subkey(KEY, "index"))

    def test_id_is_stable_and_depends_on_every_part(self):
        nk = opaque.subkey(KEY, "names")
        base = opaque.file_id(nk, "b", "p", 1, b"x")
        self.assertEqual(base, opaque.file_id(nk, "b", "p", 1, b"x"))
        for changed in (opaque.file_id(nk, "c", "p", 1, b"x"),
                        opaque.file_id(nk, "b", "q", 1, b"x"),
                        opaque.file_id(nk, "b", "p", 0, b"x"),
                        opaque.file_id(nk, "b", "p", 1, b"y"),
                        opaque.file_id(opaque.subkey(bytes(32), "names"),
                                       "b", "p", 1, b"x")):
            self.assertNotEqual(base, changed)

    def test_known_vector(self):
        # guards the id derivation: a change here orphans every site
        nk = opaque.subkey(KEY, "names")
        self.assertEqual(opaque.file_id(nk, "b", "p", 1, b"x"),
                         "7f2197da7c421e9e97670eb395289b2c")

    def test_seal_unseal_round_trip_and_tamper(self):
        for compress in (True, False):
            blob = opaque.seal(KEY, b"hello" * 50, compress)
            self.assertEqual(opaque.unseal(KEY, blob), b"hello" * 50)
        blob = bytearray(opaque.seal(KEY, b"hello", True))
        blob[6] ^= 1                       # the key id is authenticated
        with self.assertRaises(Exception):
            opaque.unseal(KEY, bytes(blob))


class PoolTests(OpaqueCase):
    def test_build_writes_only_opaque_files(self):
        self.run_build()
        names = {p.name for p in self.site.iterdir()}
        self.assertEqual(names, {
            "bin", "index.enc", "boot.json", "r.html", "q.html",
            ".itemrc", ".grouprc", ".nojekyll"})
        for ref in self.pool_files():
            self.assertRegex(ref, opaque.REF_PATTERN)
            self.assertNotIn("alpha", ref)

    def test_boot_json_holds_no_book_or_subject_name(self):
        self.run_build()
        boot = json.loads((self.site / "boot.json").read_text())
        self.assertEqual(sorted(boot), ["theme"])
        self.assertEqual(opaque.read_version(self.site, ".itemrc"), "3.4.0")
        self.assertEqual(opaque.read_version(self.site, ".grouprc"), "2")
        text = (self.site / "boot.json").read_text()
        for word in ("My Subject", "Alpha"):
            self.assertNotIn(word, text)

    def test_nothing_readable_outside_boot_and_subject(self):
        self.run_build()
        for path in list(self.site.glob("bin/??/*.bin")) + [self.site / "index.enc"]:
            blob = path.read_bytes()
            self.assertEqual(blob[:4], b"RQE1")
            for word in (b"Alpha", b"alpha", b"My Subject", b"PNG"):
                self.assertNotIn(word, blob)

    def test_index_maps_every_file_and_decrypts_back(self):
        self.run_build()
        index = opaque.read_index(self.site, KEY)
        self.assertEqual(index["format"], 2)
        self.assertEqual(index["subject"], {"title": "My Subject"})
        book = index["books"][0]
        self.assertEqual((book["repo"], book["title"], book["summary"]),
                         ("alpha", "Alpha Title", "About it"))
        self.assertRegex(book["id"], r"^[0-9a-f]{20}$")
        self.assertEqual(book["cover"], "media/alpha_front_cover.enc")
        self.assertEqual(sorted(book["views"]), ["quiz", "read"])
        self.assertEqual(sorted(book["files"]), [
            "data/ch/c01.enc", "data/manifest.enc",
            "media/alpha_front_cover.enc", "quiz/media/u01.enc",
            "quiz/questions.enc"])
        ref = book["files"]["quiz/media/u01.enc"]
        self.assertEqual(opaque.unseal(KEY, (self.site / ref).read_bytes()),
                         b"\x89PNG-old")
        self.assertEqual(sorted(book["files"].values()), self.pool_files())

    def test_second_run_writes_nothing_and_keeps_names(self):
        self.run_build()
        before = {p: (self.site / p).read_bytes() for p in self.pool_files()}
        self.run_build()
        after = {p: (self.site / p).read_bytes() for p in self.pool_files()}
        self.assertEqual(before, after)

    def test_changed_file_gets_new_name_and_old_one_is_swept(self):
        self.run_build()
        before = set(self.pool_files())
        (self.book / "data/ch/c01.enc").write_bytes(
            opaque.seal(KEY, b'{"nodes": [1]}', True))
        self.run_build()
        after = set(self.pool_files())
        self.assertEqual(len(before - after), 1)
        self.assertEqual(len(after - before), 1)

    def test_no_sweep_keeps_orphans(self):
        self.run_build()
        (self.book / "data/ch/c01.enc").write_bytes(
            opaque.seal(KEY, b'{"nodes": [1]}', True))
        self.run_build("--no-sweep")
        self.assertEqual(len(self.pool_files()), 6)

    def test_removed_book_file_is_swept_and_empty_folder_goes(self):
        self.run_build()
        (self.book / "data/ch/c01.enc").unlink()
        self.run_build()
        self.assertEqual(len(self.pool_files()), 4)
        for folder in self.site.glob("??"):
            self.assertTrue(any(folder.iterdir()))

    def test_subject_json_is_removed_and_settings_survive(self):
        self.run_build()
        self.assertFalse((self.site / "subject.json").exists())
        self.run_build()
        index = opaque.read_index(self.site, KEY)
        self.assertEqual(index["subject"], {"title": "My Subject"})
        boot = json.loads((self.site / "boot.json").read_text())
        self.assertEqual(opaque.read_version(self.site, ".itemrc"), "3.4.0")
        self.assertEqual(boot, {"theme": {"name": "plain"}})

    def test_template_tag_option_updates_boot_and_pages(self):
        self.run_build()
        build_pool.main(["--key", str(self.keyfile), "--site",
                         str(self.site), "--template-tag", "3.5.0"])
        self.assertEqual(opaque.read_version(self.site, ".itemrc"), "3.5.0")
        self.assertIn("@3.5.0/js/pool-files.js",
                      (self.site / "r.html").read_text())

    def test_publishing_one_book_keeps_the_others(self):
        other = self.tmp / "beta"
        make_book(other, "beta", "Beta")
        build_pool.main(["--key", str(self.keyfile), "--site",
                         str(self.site), str(other)])
        self.run_build()                        # publishes alpha only
        index = opaque.read_index(self.site, KEY)
        self.assertEqual([b["repo"] for b in index["books"]],
                         ["alpha", "beta"])
        self.assertEqual(len(self.pool_files()), 10)
        for book in index["books"]:
            for ref in book["files"].values():
                self.assertTrue((self.site / ref).is_file())

    def test_remove_drops_a_book_and_its_files(self):
        self.run_build()
        build_pool.main(["--key", str(self.keyfile), "--site",
                         str(self.site), "--remove", "alpha"])
        self.assertEqual(opaque.read_index(self.site, KEY)["books"], [])
        self.assertEqual(self.pool_files(), [])

    def test_id_is_random_stable_and_not_the_name(self):
        self.run_build()
        first = opaque.read_index(self.site, KEY)["books"][0]["id"]
        self.assertNotIn("alpha", first)
        self.run_build()                        # publish the book again
        self.assertEqual(
            opaque.read_index(self.site, KEY)["books"][0]["id"], first)
        other_site = self.tmp / "site2"
        other_site.mkdir()
        build_pool.main(["--key", str(self.keyfile), "--site",
                         str(other_site), "--template-tag", "3.4.0",
                         str(self.book)])
        second = opaque.read_index(other_site, KEY)["books"][0]["id"]
        self.assertNotEqual(first, second)

    def test_a_book_with_the_older_id_gets_a_random_one(self):
        self.run_build()
        index = opaque.read_index(self.site, KEY)
        book = index["books"][0]
        old = {k: v for k, v in book.items() if k != "repo"}
        old["id"] = "alpha"                     # how older sites kept it
        index["books"] = [old]
        opaque.write_index(self.site, KEY, index)
        build_pool.main(["--key", str(self.keyfile), "--site",
                         str(self.site)])       # nothing new: just migrate
        now = opaque.read_index(self.site, KEY)["books"][0]
        self.assertEqual(now["repo"], "alpha")
        self.assertRegex(now["id"], r"^[0-9a-f]{20}$")
        build_pool.main(["--key", str(self.keyfile), "--site",
                         str(self.site)])
        self.assertEqual(
            opaque.read_index(self.site, KEY)["books"][0]["id"], now["id"])

    def test_remove_works_by_id_too(self):
        self.run_build()
        book_id = opaque.read_index(self.site, KEY)["books"][0]["id"]
        build_pool.main(["--key", str(self.keyfile), "--site",
                         str(self.site), "--remove", book_id])
        self.assertEqual(opaque.read_index(self.site, KEY)["books"], [])

    def test_same_content_in_two_books_has_two_names(self):
        other = self.tmp / "beta"
        make_book(other, "beta", "Beta")
        build_pool.main(["--key", str(self.keyfile), "--site",
                         str(self.site), str(self.book), str(other)])
        index = opaque.read_index(self.site, KEY)
        refs = [r for b in index["books"] for r in b["files"].values()]
        self.assertEqual(len(refs), len(set(refs)))


class PagesAndBrowserCodeTests(OpaqueCase):
    def test_pages_name_no_book_and_load_the_pool_script(self):
        self.run_build()
        for name in ("r.html", "q.html"):
            text = (self.site / name).read_text()
            self.assertIn('name="pool-index" content="index.enc"', text)
            self.assertIn("@3.4.0/js/pool-files.js", text)
            self.assertIn("<title>Loading</title>", text)
            for word in ("Alpha", "alpha", "My Subject"):
                self.assertNotIn(word, text)

    def test_real_browser_code_opens_the_python_output(self):
        import shutil as sh
        import subprocess
        if not sh.which("node"):
            self.skipTest("node is not installed")
        self.run_build()
        book_id = opaque.read_index(self.site, KEY)["books"][0]["id"]
        out = subprocess.run(
            ["node", str(ROOT / "tests" / "pool-e2e.js"), str(self.site),
             KEY.hex(), book_id], capture_output=True, text=True, check=True)
        found = json.loads(out.stdout)
        self.assertEqual(found["title"], "Alpha Title")
        self.assertEqual(found["files"]["quiz/media/u01.enc"], "\x89PNG-old")
        self.assertEqual(found["files"]["quiz/questions.enc"],
                         '{"questions": []}')


class RekeyTests(OpaqueCase):
    def test_another_key_stops_with_a_clear_message(self):
        self.run_build()
        other = self.site.parent / "other.key"
        other.write_text(("ab" * 32))
        with self.assertRaises(SystemExit) as caught:
            build_pool.main(["--key", str(other), "--site", str(self.site),
                             str(self.book)])
        self.assertIn("another key", str(caught.exception))
        self.assertIn("reset", str(caught.exception))


class CheckSiteTests(OpaqueCase):
    def setUp(self):
        super().setUp()
        self.run_build()
        (self.site / "subject.json").unlink(missing_ok=True)
        (self.site / "index.html").write_text("<html><body></body></html>")
        import os
        os.environ["TEST_KEY"] = KEY.hex()

    def check(self, *extra):
        return check_site.main([str(self.site), "--key-env", "TEST_KEY",
                                *extra])

    def problems(self, *extra):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.check(*extra)
        return code, [l for l in out.getvalue().splitlines()
                      if l.startswith("PROBLEM")]

    def test_key_doc_meta_may_name_the_subject(self):
        import os
        index = opaque.read_index(self.site, KEY)
        name = index["books"][0]["repo"]
        (self.site / "index.html").write_text(
            '<html><head><meta name="key-doc" content="%s"></head></html>'
            % name)
        code, problems = self.problems()
        self.assertEqual((code, problems), (0, []))
        (self.site / "index.html").write_text(
            "<html><body>%s</body></html>" % name)
        code, problems = self.problems()
        self.assertEqual(code, 1)

    def test_a_clean_site_passes(self):
        code, problems = self.problems()
        self.assertEqual((code, problems), (0, []))

    def test_unexpected_top_level_items_fail(self):
        for name in ("subject.json", "books.json", "alpha"):
            path = self.site / name
            path.mkdir() if name == "alpha" else path.write_text("{}")
            code, problems = self.problems()
            self.assertEqual(code, 1, name)
            self.assertTrue(any(name in p for p in problems), name)
            shutil.rmtree(path) if path.is_dir() else path.unlink()

    def test_plain_file_in_the_pool_fails(self):
        ref = self.pool_files()[0]
        (self.site / ref).write_bytes(b"RQE1" + b"a" * 200 + b"x" * 300)
        code, problems = self.problems()
        self.assertEqual(code, 1)
        self.assertTrue(any("does not look encrypted" in p for p in problems)
                        or any("subject key" in p for p in problems))

    def test_readable_pool_file_fails_even_without_a_key(self):
        ref = self.pool_files()[0]
        (self.site / ref).write_bytes(b"RQE1\0s001" + b"abc " * 400)
        import os
        os.environ.pop("TEST_KEY")
        code, problems = self.problems()
        self.assertEqual(code, 1)
        self.assertTrue(any("does not look encrypted" in p for p in problems))

    def test_wrong_pool_name_fails(self):
        (self.site / "bin" / "ab").mkdir(parents=True, exist_ok=True)
        (self.site / "bin" / "ab" / "alpha.bin").write_bytes(b"RQE1" * 20)
        code, problems = self.problems()
        self.assertTrue(any("not a pool file name" in p for p in problems))

    def test_orphan_and_missing_files_fail(self):
        ref = self.pool_files()[0]
        (self.site / ref).rename(self.site / "ab_x")
        (self.site / "ab_x").rename(self.tmp / "gone")
        (self.site / "bin" / "ab").mkdir(parents=True, exist_ok=True)
        extra = self.site / "bin" / "ab" / ("0" * 30 + ".bin")
        extra.write_bytes(opaque.seal(KEY, b"orphan", True))
        code, problems = self.problems()
        self.assertTrue(any("missing" in p for p in problems))
        self.assertTrue(any("orphan" in p for p in problems))

    def test_old_layout_folder_at_the_top_is_reported(self):
        (self.site / "ab").mkdir()
        code, problems = self.problems()
        self.assertEqual(code, 1)
        self.assertTrue(any("old layout" in p for p in problems))

    def test_book_name_in_a_page_or_file_name_fails(self):
        (self.site / "index.html").write_text("<h1>Alpha Title</h1>")
        (self.site / ".theme").mkdir()
        (self.site / ".theme" / "my-subject.svg").write_text("<svg/>")
        code, problems = self.problems()
        self.assertTrue(any(p.startswith("PROBLEM: index.html contains hidden")
                            for p in problems))
        self.assertTrue(any("my-subject.svg" in p for p in problems))
        for line in problems:          # a log must not repeat the name
            self.assertNotIn("Alpha", line)
            self.assertNotIn("alpha title", line.lower())

    def test_isbn_and_extra_terms_fail(self):
        (self.site / "index.html").write_text("978-0-306-40615-7 secretword")
        terms = self.tmp / "terms.txt"
        terms.write_text("secretword\n")
        code, problems = self.problems("--terms", str(terms))
        self.assertTrue(any("ISBN" in p for p in problems))
        self.assertTrue(any("index.html contains hidden name" in p
                            for p in problems))
        self.assertFalse(any("secretword" in p for p in problems))

    def test_boot_json_with_a_title_fails(self):
        (self.site / "boot.json").write_text(
            '{"theme": {}, "title": "x"}')
        code, problems = self.problems()
        self.assertTrue(any("boot.json may hold only" in p for p in problems))

    def test_a_bad_version_file_fails(self):
        (self.site / ".itemrc").write_text("3\n")
        (self.site / ".grouprc").write_text("main\n")
        code, problems = self.problems()
        self.assertTrue(any(".itemrc must hold" in p for p in problems))
        self.assertTrue(any(".grouprc must hold" in p for p in problems))

    def test_an_older_site_with_versions_in_boot_json_is_still_valid_and_migrates(self):
        (self.site / ".itemrc").unlink()
        (self.site / ".grouprc").unlink()
        (self.site / "boot.json").write_text(
            '{"templateTag": "3.4.0", "uiVersion": "2"}')
        code, problems = self.problems()
        self.assertEqual(problems, [])
        build_pool.main(["--key", str(self.keyfile), "--site", str(self.site)])
        self.assertEqual(opaque.read_version(self.site, ".itemrc"), "3.4.0")
        self.assertEqual(opaque.read_version(self.site, ".grouprc"), "2")
        self.assertEqual(json.loads((self.site / "boot.json").read_text()), {})

    def test_readme_and_claude_notes_are_allowed_but_other_files_are_not(self):
        (self.site / "README.md").write_text("A template.\n")
        (self.site / "CLAUDE.md").write_text("Notes.\n")
        code, problems = self.problems()
        self.assertEqual(problems, [])
        (self.site / "notes.txt").write_text("x")
        code, problems = self.problems()
        self.assertTrue(any("notes.txt" in p for p in problems))

    def test_dot_files_need_nojekyll(self):
        (self.site / ".nojekyll").unlink()
        code, problems = self.problems()
        self.assertTrue(any(".nojekyll is missing" in p for p in problems))

    def test_a_file_made_with_another_key_fails(self):
        ref = self.pool_files()[0]
        (self.site / ref).write_bytes(opaque.seal(bytes(32), b"x" * 900, True))
        code, problems = self.problems()
        self.assertTrue(any("subject key" in p for p in problems))

    def test_commit_message_is_checked(self):
        import subprocess
        run = lambda *a: subprocess.run(["git", "-C", str(self.site), *a],
                                        check=True, capture_output=True)
        run("init", "-q")
        run("add", "-A")
        run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
            "-m", "Add Alpha Title")
        code, problems = self.problems("--git-log")
        self.assertTrue(any("commit message" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
