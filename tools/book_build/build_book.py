#!/usr/bin/env python3
"""Build the publishable, encrypted site of one private book repo.

    SUBJECT_KEY=<64 hex or base64> python3 tools/book_build/build_book.py \\
        --book PATH_TO_BOOK_REPO [--epub references/Book.epub] \\
        [--notes-epub references/notes.epub] --template-tag 3.0.0 \\
        --out SITE_DIR

Writes into SITE_DIR exactly what a subject site accepts for one book:
index.html, read.html and quiz.html (small pages that load everything from
the CDN at the template tag), book.json, data/ and media/ (the reader) and
quiz/ (questions.enc and the quiz pictures); encrypted files only. Without
--epub the book has only the quiz. Plain extracts and the key file live in a scratch
folder that is deleted at the end; the key is read from the environment
variable SUBJECT_KEY and never written next to the output.

Steps: unzip the book EPUB, cut it into chapters / sections / subtopics
(extract_epub.py), match ideas and questions from questions.json and encrypt
(build_site.py), add the notes EPUB (add_notes.py), write the outline
(finalize_manifest.py), encrypt questions.json and the quiz pictures with
the same key (quiz/), then write the pages and book.json.
"""
import argparse
import json
import base64
import binascii
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import encrypt_media  # noqa: E402  (quiz picture format, tools/)
import media_types  # noqa: E402
import make_subject_pages  # noqa: E402
sys.path.insert(0, str(HERE))
import encfmt  # noqa: E402
TAG = re.compile(r"^\d+\.\d+\.\d+$")
ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
def run(*args):
    """Run a build step; stop the build when it fails."""
    result = subprocess.run([sys.executable, *map(str, args)])
    if result.returncode != 0:
        sys.exit("build step failed: %s" % Path(str(args[0])).name)


def parse_key(text):
    """The subject key as 64 hex characters, from hex or from 32 bytes in base64."""
    text = text.strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return text.lower()
    try:
        raw = base64.b64decode(text.replace("-", "+").replace("_", "/"), validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw.hex() if len(raw) == 32 else None


def book_media(book, bank):
    """The plain media files of the book repo: {id: path}.

    A file media/<id>.<ext> belongs to the entry with that id; the extension
    must be the one the entry's type gives, because that is the name the
    quiz asks for. Stops on an old encrypted media/*.enc file (convert the
    book with tools/plain_media.py), two files with one id, or a file whose
    extension does not match its entry.
    """
    source = book / "media"
    old = sorted(source.rglob("*.enc")) if source.is_dir() else []
    if old:
        sys.exit("media/ still holds encrypted files (%s...): convert the "
                 "book with tools/plain_media.py first" %
                 old[0].relative_to(book).as_posix())
    files = {}
    for path in sorted(source.rglob("*")) if source.is_dir() else []:
        if not path.is_file() or path.name.startswith("."):
            continue
        ext = path.suffix[1:]
        if ext not in media_types.TYPES:
            print("skipped media/%s: not a picture or clip type" %
                  path.relative_to(source).as_posix())
            continue
        file_id = path.relative_to(source).with_suffix("").as_posix()
        if file_id in files:
            sys.exit("two files for the media id %s" % file_id)
        files[file_id] = path
    for entry in media_types.entries(bank):
        want = media_types.extension_of(entry.get("type"))
        have = files.get(entry["id"])
        if want is None:
            sys.exit("media %s has the type %s, which is not supported" %
                     (entry["id"], entry.get("type")))
        if have is None:
            print("warning: no file media/%s.%s for %s" %
                  (entry["id"], want, entry["id"]))
        elif have.suffix[1:] != want:
            sys.exit("media/%s is a .%s file but its entry says %s (.%s): "
                     "rename the file or fix the type" %
                     (have.relative_to(source).as_posix(), have.suffix[1:],
                      entry.get("type") or media_types.DEFAULT_TYPE, want))
    return files


def build_quiz(book, bank, key_hex, out):
    """Write quiz/questions.enc and quiz/media/ (same subject key).

    The book repo's plain media/<id>.<ext> files are encrypted with the
    subject key as quiz/media/<id>.enc.
    @return: number of pictures written
    """
    files = book_media(book, bank)
    bank = dict(bank, mediaBase="quiz/media/")
    quiz_dir = out / "quiz"
    (quiz_dir / "media").mkdir(parents=True)
    (quiz_dir / "questions.enc").write_bytes(encfmt.encrypt(
        json.dumps(bank, ensure_ascii=False).encode(),
        bytes.fromhex(key_hex)))
    key, total = bytes.fromhex(key_hex), 0
    for file_id, path in files.items():
        plain = path.read_bytes()
        if len(plain) > encrypt_media.LIMITS["one file"]:
            sys.exit("%s is %s bytes: too big" % (path.name, format(len(plain), ",")))
        total += len(plain)
        target = quiz_dir / "media" / (file_id + ".enc")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encrypt_media.encrypt(key, file_id, plain))
    if total > encrypt_media.LIMITS["book"]:
        sys.exit("the media of this book is %s bytes: over the book limit" %
                 format(total, ","))
    return len(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--book", type=Path, required=True)
    parser.add_argument("--epub", help="path inside the book repo; "
                        "leave out for a quiz-only book")
    parser.add_argument("--notes-epub", help="path inside the book repo")
    parser.add_argument("--template-tag", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    key = parse_key(os.environ.get("SUBJECT_KEY", ""))
    if not key:
        sys.exit("SUBJECT_KEY must be 32 bytes: 64 hex characters or base64")
    if not TAG.match(args.template_tag):
        sys.exit("the template tag must look like 0.1.0 (no \"v\", no branch)")
    book = args.book.resolve()
    out = args.out.resolve()
    if out == book or book in out.parents:
        sys.exit("--out must be outside the book repo")
    bank = json.loads((book / "questions.json").read_text(encoding="utf-8"))
    if not ID.match(bank.get("id", "")):
        sys.exit("questions.json needs a lower-case \"id\"")
    # A book with no questions has no quiz view, so the hub greys out Test;
    # one with no EPUB has no read view, so it greys out Read. A book with
    # neither is still listed (title and card only) so its place in the
    # library can be checked.
    has_quiz = bool(bank.get("questions"))
    if not has_quiz and not args.epub:
        print("note: no EPUB and no questions: the book is listed with "
              "Read and Test both off")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    scratch = Path(tempfile.mkdtemp(prefix="book-build-"))
    try:
        keyfile = scratch / "subject.key"
        keyfile.write_text(key)
        os.chmod(keyfile, 0o600)
        work = scratch / "work"
        work.mkdir()
        views = ["quiz"] if has_quiz else []
        if args.epub:
            views.insert(0, "read")
            epub_dir = scratch / "epub"
            with zipfile.ZipFile(book / args.epub) as archive:
                archive.extractall(epub_dir)
            chapters = book / "book-structure" / "chapters.json"
            if not chapters.exists():
                # add-structure's unit list is enough to read a book before
                # any notes or questions exist.
                chapters = book / "book-structure" / "chapters.skeleton.json"
            run(HERE / "extract_epub.py", epub_dir, chapters, work)
            run(HERE / "build_site.py", book, work, out, keyfile, epub_dir)
            if args.notes_epub:
                run(HERE / "add_notes.py", "--site", out, "--key", keyfile,
                    "--notes-epub", book / args.notes_epub,
                    "--report", scratch / "notes-report.json")
            run(HERE / "finalize_manifest.py", "--site", out,
                "--key", keyfile)
        pictures = build_quiz(book, bank, key, out) if has_quiz else 0
        for name, text in make_subject_pages.make_pages(
                args.template_tag, bank["book"], views).items():
            (out / name).write_text(text, encoding="utf-8")
        info = {"id": bank["id"], "title": bank["book"],
                "views": {v: "index.html?view=" + v for v in views}}
        (out / "book.json").write_text(
            json.dumps(info, ensure_ascii=False) + "\n", encoding="utf-8")
        # the match report is useful in the job log; it holds counts only
        report = work / "match-report.json"
        if report.exists():
            data = json.loads(report.read_text(encoding="utf-8"))
            print("unmatched ideas/questions:",
                  sum(u["unmatched_items"] for u in data["units"].values()))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    allowed = {"index.html", "read.html", "quiz.html", "book.json",
               "data", "media", "quiz"}
    leftovers = [p.name for p in out.iterdir() if p.name not in allowed]
    plain = [str(p.relative_to(out)) for part in ("data", "media", "quiz")
             if (out / part).is_dir()
             for p in (out / part).rglob("*")
             if p.is_file() and p.suffix != ".enc"]
    if leftovers or plain:
        sys.exit("refusing: unexpected output %s" % (leftovers + plain))
    print("Built %s: views %s, %d data files, %d reader media, "
          "%d quiz pictures" % (
              bank["id"], "+".join(views),
              sum(1 for _ in (out / "data").rglob("*.enc")),
              sum(1 for _ in (out / "media").rglob("*.enc")), pictures))
    return 0


if __name__ == "__main__":
    sys.exit(main())
