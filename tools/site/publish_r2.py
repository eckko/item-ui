#!/usr/bin/env python3
"""Publish one built book to the private R2 bucket behind the Worker.

    python3 publish_r2.py --built BUILT_DIR --book BOOK_REPO
        [--tag SUBJECT]... [--author NAME] [--dry-run] [--no-ping]
        [--require-id]

BUILT_DIR is what tools/book_build/build_book.py writes. BOOK_REPO is the
private book repo (it holds .onebook.json and questions.json). The key is
read from LIBRARY_KEY (or SUBJECT_KEY): 64 hex or base64.

Each file is decrypted, then stored again as RQE1 under

    p/<book id>/<the file's own name>      e.g. p/<id>/data/ch/c01.enc

so the reader keeps asking for data/manifest.enc, media/<id>.enc and
quiz/... under one base address. A big question bank is cut into
quiz/q/<unit>.enc (split_questions.py) and quiz/parts.enc lists them.
Two more files sit at the top: cover.enc (a small webp, when the book has a
cover and Pillow is installed) and card.enc (title, author, tags, counts).

A file's change check is the content id of opaque.py (an HMAC of book id,
path and content): it is stored as the object's metadata "ph", and an
object whose "ph" matches is not uploaded again. Order: chapters, pictures
and quiz files, then data/manifest.enc, cover.enc and card.enc last, so the
Worker never lists a book that is half uploaded. Objects of the book that
the new set does not name are deleted at the end.

The book id comes from .onebook.json in BOOK_REPO ({"id": "<32 hex>"}). When
it is missing the tool makes one and asks you to commit it; with
--require-id (used in CI) it stops instead, because a new id on every run
would publish the book again and again.

Nothing here prints a title, a file name or a key: counts only.
Environment: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
R2_BUCKET and, for the ping, WORKER_URL.
"""

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_pool  # noqa: E402
import opaque  # noqa: E402
import split_questions  # noqa: E402

ID_FILE = ".onebook.json"
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
PREFIX = "p/"
PARTS_PATH = "quiz/parts.enc"
MANIFEST_PATH = "data/manifest.enc"
COVER_PATH = "cover.enc"
CARD_PATH = "card.enc"
COVER_SIZE = (200, 300)
FOLDERS = ("data", "media", "quiz")
TEXT_FILES = ("data/", "quiz/")
NOT_FOUND = ("404", "NoSuchKey", "NotFound")


class PublishError(Exception):
    """A problem that stops the publish; the message names no book."""


def book_id_of(book, require):
    """The book's permanent 32-hex id from .onebook.json.

    @param book: the book repo folder
    @param require: stop instead of creating the file
    @return: (id, created) where created is True when the file was made
    """
    path = Path(book) / ID_FILE
    if path.is_file():
        found = json.loads(path.read_text(encoding="utf-8")).get("id", "")
        if not ID_PATTERN.match(found):
            raise PublishError("%s must hold {\"id\": \"<32 hex>\"}" % ID_FILE)
        return found, False
    if require:
        raise PublishError(
            "%s is missing: run publish_r2.py once on your computer and "
            "commit the file it makes" % ID_FILE)
    made = secrets.token_hex(16)
    path.write_text(json.dumps({"id": made}) + "\n", encoding="utf-8")
    return made, True


def collect_files(built, key):
    """Every encrypted file of the built book, decrypted.

    @param built: the folder build_book.py wrote
    @param key: the 32-byte key
    @return: dict {path inside the book: (plain bytes, compress)}
    @raise PublishError: a plain file, or a file the key cannot open
    """
    built = Path(built)
    files = {}
    for folder in FOLDERS:
        for path in sorted((built / folder).rglob("*")):
            if not path.is_file():
                continue
            own = path.relative_to(built).as_posix()
            if path.suffix != ".enc":
                raise PublishError("a plain file in %s/ (refused)" % folder)
            try:
                plain = build_pool.read_plain(
                    key, path, build_pool.picture_id(own))
            except Exception:
                raise PublishError(
                    "a file in %s/ cannot be opened with the key" % folder)
            files[own] = (plain, own.startswith(TEXT_FILES))
    return files


def split_bank(files):
    """Cut a big question bank into per-unit files, as build_pool does.

    @param files: the dict from collect_files (changed in place)
    @return: the part paths in book order ([] when the bank is one file)
    """
    entry = files.get("quiz/questions.enc")
    if not entry:
        return []
    parts = split_questions.chunks(json.loads(entry[0]))
    if not parts:
        return []
    del files["quiz/questions.enc"]
    for path, body in parts:
        files[path] = (split_questions.dump(body), True)
    names = [path for path, _ in parts]
    files[PARTS_PATH] = (json.dumps(names).encode("utf-8"), True)
    return names


def question_count(files, part_names):
    """How many questions the book has.

    @param files: the dict from collect_files
    @param part_names: the part paths from split_bank
    @return: an int
    """
    paths = part_names or ["quiz/questions.enc"]
    total = 0
    for path in paths:
        if path in files:
            total += len(json.loads(files[path][0]).get("questions", []))
    return total


def make_cover(files):
    """A small webp of the book's cover, or None.

    @param files: the dict from collect_files
    @return: webp bytes or None (no cover, or Pillow is not installed)
    """
    manifest = files.get(MANIFEST_PATH)
    if not manifest:
        return None
    cover = json.loads(manifest[0]).get("book", {}).get("cover")
    if not cover:
        return None
    source = files.get("media/" + cover.replace("/", "_") + ".enc")
    if not source:
        return None
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is not installed: no cover published")
        return None
    image = Image.open(io.BytesIO(source[0])).convert("RGB")
    image.thumbnail(COVER_SIZE)
    out = io.BytesIO()
    image.save(out, "WEBP", quality=80)
    return out.getvalue()


def new_card(files, built, book, tags, author, part_names, added):
    """The card's content: what the home page shows about the book.

    @param files: the dict from collect_files
    @param built: the built book folder
    @param book: the book repo folder
    @param tags: subject tags
    @param author: the author, "" when unknown
    @param part_names: the question part paths
    @param added: unix time the book was first published
    @return: dict (the "v" version is filled in by the caller)
    """
    info = json.loads((Path(built) / "book.json").read_text("utf-8"))
    chapters = sum(1 for path in files if path.startswith("data/ch/"))
    bank = Path(book) / "questions.json"
    if not author and bank.is_file():
        author = json.loads(bank.read_text("utf-8")).get("author", "")
    return {"t": info.get("title", ""), "a": author, "tags": list(tags),
            "ch": chapters, "q": question_count(files, part_names),
            "added": added, "views": sorted(info.get("views", {}))}


def is_missing(error):
    """True when an S3 error means the object does not exist."""
    code = str(getattr(error, "response", {}).get("Error", {}).get("Code"))
    return code in NOT_FOUND


def stored_meta(client, bucket, key):
    """The metadata "ph" of an object, or None when it does not exist."""
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except Exception as error:
        if is_missing(error):
            return None
        raise
    return head.get("Metadata", {}).get("ph", "")


def first_added(client, bucket, base, key):
    """When the book was first published, from the stored card, or now."""
    try:
        got = client.get_object(Bucket=bucket, Key=base + CARD_PATH)
        card = json.loads(opaque.unseal(key, got["Body"].read()))
        return int(card.get("added", time.time()))
    except Exception:
        return int(time.time())


def list_keys(client, bucket, base):
    """Every object key under base."""
    keys, token = [], None
    while True:
        args = {"Bucket": bucket, "Prefix": base}
        if token:
            args["ContinuationToken"] = token
        page = client.list_objects_v2(**args)
        keys += [item["Key"] for item in page.get("Contents", [])]
        if not page.get("IsTruncated"):
            return keys
        token = page["NextContinuationToken"]


def upload_order(paths):
    """Paths in upload order: the manifest, cover and card come last."""
    last = {MANIFEST_PATH: 1, COVER_PATH: 2, CARD_PATH: 3}
    return sorted(paths, key=lambda path: (last.get(path, 0), path))


def publish(client, bucket, built, book, key, book_id, tags=(),
            author="", dry_run=False):
    """Upload the changed files of one book and delete its orphans.

    @return: dict of counts {"uploaded", "unchanged", "deleted", "files"}
    """
    files = collect_files(built, key)
    part_names = split_bank(files)
    name_key = opaque.subkey(key, "names")
    base = PREFIX + book_id + "/"
    cover = make_cover(files)
    if cover:
        files[COVER_PATH] = (cover, False)
    ids = {path: opaque.file_id(name_key, book_id, path,
                                1 if compress else 0, plain)
           for path, (plain, compress) in files.items()}
    version = hashlib.sha256("".join(
        ids[path] for path in sorted(ids)).encode()).hexdigest()[:16]
    added = first_added(client, bucket, base, key)
    card = new_card(files, built, book, tags, author, part_names, added)
    card["v"] = version
    card_bytes = json.dumps(card, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")).encode("utf-8")
    files[CARD_PATH] = (card_bytes, True)
    ids[CARD_PATH] = opaque.file_id(name_key, book_id, CARD_PATH, 1,
                                    card_bytes)
    counts = {"uploaded": 0, "unchanged": 0, "deleted": 0,
              "files": len(files)}
    for path in upload_order(files):
        if stored_meta(client, bucket, base + path) == ids[path]:
            counts["unchanged"] += 1
            continue
        counts["uploaded"] += 1
        if dry_run:
            continue
        plain, compress = files[path]
        client.put_object(
            Bucket=bucket, Key=base + path,
            Body=opaque.seal(key, plain, compress),
            ContentType="application/octet-stream",
            Metadata={"ph": ids[path]})
    wanted = {base + path for path in files}
    for stale in sorted(set(list_keys(client, bucket, base)) - wanted):
        counts["deleted"] += 1
        if not dry_run:
            client.delete_object(Bucket=bucket, Key=stale)
    return counts


def ping(worker_url):
    """Ask the Worker to rebuild index.json now; never stops the publish.

    @return: a short text for the log
    """
    try:
        request = urllib.request.Request(
            worker_url.rstrip("/") + "/ping", method="POST", data=b"")
        with urllib.request.urlopen(request, timeout=20) as reply:
            return "ping %d" % reply.status
    except Exception as error:
        return "ping failed (%s); the cron rebuilds within 15 minutes" % (
            type(error).__name__)


def make_client():
    """The S3 client for R2, from the environment."""
    import boto3
    account = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3", endpoint_url="https://%s.r2.cloudflarestorage.com" % account,
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto")


def main(argv=None, client=None):
    """Command line entry; returns the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--built", required=True)
    parser.add_argument("--book", required=True)
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--author", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-ping", action="store_true")
    parser.add_argument("--require-id", action="store_true")
    args = parser.parse_args(argv)
    text = os.environ.get("LIBRARY_KEY") or os.environ.get("SUBJECT_KEY", "")
    if not text.strip():
        sys.exit("set LIBRARY_KEY (or SUBJECT_KEY)")
    try:
        key = opaque.parse_key(text)
        book_id, created = book_id_of(args.book, args.require_id)
        client = client or make_client()
        counts = publish(client, os.environ["R2_BUCKET"], args.built,
                         args.book, key, book_id, args.tag, args.author,
                         args.dry_run)
    except PublishError as error:
        sys.exit("refused: %s" % error)
    if created:
        print("made %s: commit it, the id never changes" % ID_FILE)
    line = "%s: %d files, uploaded %d, unchanged %d, deleted %d" % (
        "dry run" if args.dry_run else "published", counts["files"],
        counts["uploaded"], counts["unchanged"], counts["deleted"])
    if not args.dry_run and not args.no_ping and os.environ.get("WORKER_URL"):
        line += ", " + ping(os.environ["WORKER_URL"])
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
