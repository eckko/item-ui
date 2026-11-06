#!/usr/bin/env python3
"""Turn built book folders into the flat pool of opaque files.

    python3 build_pool.py --key KEYFILE --site SITE BOOK_FOLDER [...]
                          [--no-sweep] [--only-move]

Each BOOK_FOLDER is what the private book build writes (see
tools/site/build_hub.py for the layout). The tool decrypts each file with
the subject key, stores it again under an opaque name (tools/site/opaque.py)
and writes, in SITE:

    bin/xx/<id>.bin   the files
    index.enc     the encrypted index: subject settings, each book with its
                  title, cover, views and the map from the book's own file
                  names to pool paths
    r.html q.html the reader and the quiz pages (opened as r.html#b=<id>);
                  they name no book
    boot.json     the sign-in theme: nothing else
    .itemrc  the exact item UI tag; .grouprc: the hub UI version

A pool that still sits in the old place (xx/<id>.bin at the top of SITE) is
moved under bin/ first, and index.enc is pointed at the new paths; --only-move
does just that and builds nothing. A file whose content did not change keeps
its name and is left alone.
Books already in the site's index.enc and not named on the command line are
kept as they are, so each private book repo can publish its own book:
a book with the same name is replaced, any other is added. A book gets a
random id the first time it is published and keeps it. --remove drops a
book, given by its name or its id. Files no book uses any more are removed (leave that out with
--no-sweep). SITE may hold subject.json (read for the title and the
settings); it is removed once its settings are in index.enc and boot.json.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import opaque  # noqa: E402
import split_questions  # noqa: E402

TEXT_FILES = ("data/", "quiz/questions.enc")
SUBJECT_PUBLIC_KEYS = ("templateTag", "uiVersion", "theme")   # never in index.enc


def read_plain(key, path, own_name):
    """The plain bytes of one stored file.

    @param key: the subject key
    @param path: the file on disk
    @param own_name: the file's name inside the book, without ".enc"; the
        older picture layout needs it as its authenticated data
    @return: bytes
    """
    blob = path.read_bytes()
    if blob[:4] == opaque.MAGIC:
        return opaque.unseal(key, blob)
    # the older picture layout: nonce first, key from HKDF "file:<id>"
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import encrypt_media
    return encrypt_media.decrypt(key, own_name, blob)


def picture_id(own_name):
    """A picture's id: its name inside media/ or quiz/media/, no ".enc"."""
    name = own_name[:-len(".enc")]
    for prefix in ("quiz/media/", "media/"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def import_book(pool, folder):
    """Store every file of one book folder in the pool.

    @param pool: an opaque.Pool
    @param folder: the book folder
    @return: the index entry of the book
    """
    info = json.loads((folder / "book.json").read_text(encoding="utf-8"))
    book_id = info["id"]
    files = {}
    question_parts = []
    for path in sorted(folder.rglob("*.enc")):
        own = path.relative_to(folder).as_posix()
        plain = read_plain(pool.key, path, picture_id(own))
        text = own.startswith(TEXT_FILES)
        if own == "quiz/questions.enc":
            parts = split_questions.chunks(json.loads(plain))
            if parts:
                # a big bank: one pool file per unit, listed in order
                for part_path, body in parts:
                    files[part_path] = pool.put(
                        book_id, part_path, split_questions.dump(body),
                        compress=True)
                question_parts = [part_path for part_path, _ in parts]
                continue
        files[own] = pool.put(book_id, own, plain, compress=text)
    # "repo" is the book's own name (what the book repo and its status
    # record are called); "id" is the random address id, set in main()
    entry = {"repo": book_id, "title": info.get("title", book_id),
             "views": sorted(info.get("views", {})), "files": files}
    if question_parts:
        entry["questionParts"] = question_parts
    manifest = files.get("data/manifest.enc")
    if manifest:
        data = json.loads(opaque.unseal(
            pool.key, (pool.root / manifest).read_bytes()))
        book = data.get("book", {})
        entry["summary"] = book.get("summary", "")
        cover = book.get("cover")
        if cover:
            entry["cover"] = "media/" + cover.replace("/", "_") + ".enc"
    return entry


def with_random_id(entry):
    """An index entry in the current shape.

    Older indexes used the book's own name as its id. Such an entry gets a
    random id (and keeps the name as "repo") the first time it is read.

    @param entry: a book entry of index.enc
    @return: the entry with "repo" and a random "id"
    """
    if "repo" in entry:
        return entry
    upgraded = dict(entry)
    upgraded["repo"] = entry["id"]
    upgraded["id"] = opaque.new_book_id()
    return upgraded



def read_existing_index(site, key):
    """The index already in the site, with a clear stop when it was made
    with a different key (a subject that is being re-keyed).

    @return: the decrypted index
    """
    try:
        return opaque.read_index(site, key)
    except Exception as error:
        raise SystemExit(
            "index.enc in this subject site cannot be read with the "
            "subject key given (%s). The site was made with another key. "
            "To change a subject's key, run Update subject with 'reset' "
            "on, which clears index.enc and the pool; every book is then "
            "published again (book ids, and so reader progress, restart)."
            % type(error).__name__)


def starting_settings(site, key):
    """The subject's settings: subject.json when the site still has it
    (first conversion), otherwise what the last run wrote (the index's
    subject part and boot.json).

    @return: dict with the title and the like, plus templateTag, uiVersion
        and theme when known
    """
    if (site / "subject.json").is_file():
        return json.loads((site / "subject.json").read_text(encoding="utf-8"))
    settings = {}
    if (site / opaque.INDEX_NAME).is_file():
        settings.update(read_existing_index(site, key).get("subject", {}))
    if (site / opaque.BOOT_NAME).is_file():
        # an older site kept its versions here; the files below win
        settings.update(json.loads(
            (site / opaque.BOOT_NAME).read_text(encoding="utf-8")))
    item = opaque.read_version(site, opaque.ITEM_VERSION_NAME)
    group = opaque.read_version(site, opaque.GROUP_VERSION_NAME)
    if item:
        settings["templateTag"] = item
    if group:
        settings["uiVersion"] = group
    return settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--key", help="key file (hex/base64); default: "
                        "the environment variable SUBJECT_KEY")
    parser.add_argument("--template-tag", help="write this exact tag to "
                        ".itemrc and into r.html and q.html")
    parser.add_argument("--site", required=True, help="the site folder")
    parser.add_argument("--no-sweep", action="store_true")
    parser.add_argument("--only-move", action="store_true",
                        help="only move an old pool under bin/; build "
                        "nothing")
    parser.add_argument("--remove", action="append", default=[],
                        metavar="ID", help="drop this book id (repeatable)")
    parser.add_argument("books", nargs="*", help="built book folders")
    args = parser.parse_args(argv)
    site = Path(args.site)
    text = Path(args.key).read_text() if args.key else os.environ.get(
        "SUBJECT_KEY", "")
    if not text.strip():
        sys.exit("give --key FILE or set SUBJECT_KEY")
    key = opaque.parse_key(text)
    if (site / opaque.INDEX_NAME).is_file():
        read_existing_index(site, key)   # a clear stop on the wrong key
    moved = opaque.move_pool_into_bin(site, key)
    if moved:
        print("pool files moved under %s/: %d" % (opaque.POOL_FOLDER, moved))
    if args.only_move:
        return 0
    subject = starting_settings(site, key)
    if args.template_tag:
        subject["templateTag"] = args.template_tag
    pool = opaque.Pool(site, key)
    entries = [import_book(pool, Path(b)) for b in args.books]
    before = []
    if (site / opaque.INDEX_NAME).is_file():
        before = [with_random_id(e) for e in
                  read_existing_index(site, key)["books"]]
    ids = {e["repo"]: e["id"] for e in before}
    for entry in entries:
        # a book that is already in the index keeps its id for good
        entry["id"] = ids.get(entry["repo"]) or opaque.new_book_id()
    replaced = {e["repo"] for e in entries}
    removed_ids = set(args.remove)
    for entry in before:
        if entry["repo"] in replaced:
            continue
        if entry["repo"] in removed_ids or entry["id"] in removed_ids:
            continue
        entries.append(entry)
        pool.used.update(entry["files"].values())
    entries.sort(key=lambda e: e["repo"])
    shown = {k: v for k, v in subject.items()
             if k not in SUBJECT_PUBLIC_KEYS}
    opaque.write_index(site, key, opaque.build_index(shown, entries))
    tag = subject.get("templateTag")
    if tag:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import make_subject_pages
        for name, text in make_subject_pages.make_pool_pages(tag).items():
            (site / name).write_text(text, encoding="utf-8")
    # GitHub Pages hides files that start with a dot (.grouprc, .itemrc)
    # from a site without this empty file
    (site / ".nojekyll").touch()
    opaque.write_boot(site, subject.get("theme"))
    if tag:
        opaque.write_version(site, opaque.ITEM_VERSION_NAME, tag)
    if subject.get("uiVersion"):
        opaque.write_version(site, opaque.GROUP_VERSION_NAME,
                             subject["uiVersion"])
    # the settings now live in index.enc and boot.json; a readable
    # subject.json would still name the subject
    (site / "subject.json").unlink(missing_ok=True)
    removed = [] if args.no_sweep else pool.sweep()
    print("files written %d, unchanged %d, removed %d, books %d"
          % (pool.written, pool.skipped, len(removed), len(entries)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
