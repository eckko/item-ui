#!/usr/bin/env python3
"""Convert an old book's encrypted media/ files to plain files (one time).

    python3 tools/plain_media.py BOOK_REPO [--dry-run]

Old books keep media/<id>.enc, hidden with a public stand-in key. Books now
keep plain media/<id>.<ext> (see ai-context/data-formats.md). This opens each
media/**/*.enc, writes media/<id>.<ext> and removes the .enc file. The
extension comes from the "type" of the entry in questions.json that uses the
id, else from the first bytes of the file; ids no entry uses are converted
too. It stops, changing nothing, when a file cannot be opened or an id would
get two files.

Legacy: this is the only place that still knows the stand-in key. Delete the
tool once every book is converted.
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import encrypt_media  # noqa: E402
import media_types  # noqa: E402

# The public stand-in key the old media/*.enc files were hidden with.
STAND_IN_KEY = hashlib.sha256(b"key-placeholder").digest()

SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"),
    (b"GIF8", "gif"), (b"OggS", "ogg"), (b"ID3", "mp3"),
)


def sniff(data):
    """The extension for a file's first bytes, or None."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[4:8] == b"ftyp":
        return "avif" if data[8:12] in (b"avif", b"avis") else "mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    head = data[:300].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2000].lower()):
        return "svg"
    for start, ext in SIGNATURES:
        if data.startswith(start):
            return ext
    return None


def convert(book, dry_run=False):
    """Convert one book repo.

    @return: list of (old path, new path) relative to the book
    """
    source = Path(book) / "media"
    old = sorted(source.rglob("*.enc")) if source.is_dir() else []
    if not old:
        return []
    bank_path = Path(book) / "questions.json"
    types = {}
    if bank_path.is_file():
        bank = json.loads(bank_path.read_text(encoding="utf-8"))
        for entry in media_types.entries(bank):
            types[entry["id"]] = entry.get("type")
    plan, taken = [], set()
    for path in old:
        file_id = path.relative_to(source).with_suffix("").as_posix()
        try:
            data = encrypt_media.decrypt(STAND_IN_KEY, file_id, path.read_bytes())
        except Exception:
            raise SystemExit("cannot open %s with the stand-in key: this "
                             "book's media was made with another key" %
                             path.relative_to(book).as_posix())
        ext = media_types.extension_of(types[file_id]) if types.get(file_id) \
            else sniff(data)
        if not ext:
            raise SystemExit("cannot tell the type of %s: add its entry's "
                             "\"type\" to questions.json" %
                             path.relative_to(book).as_posix())
        target = source / (file_id + "." + ext)
        if target in taken or target.exists():
            raise SystemExit("%s would be written twice" %
                             target.relative_to(book).as_posix())
        taken.add(target)
        plan.append((path, target, data))
    if not dry_run:
        for path, target, data in plan:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            path.unlink()
    return [(p.relative_to(book).as_posix(), t.relative_to(book).as_posix())
            for p, t, _ in plan]


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    if len(args) != 1:
        raise SystemExit(__doc__)
    done = convert(args[0], dry)
    for old, new in done:
        print("%s -> %s" % (old, new))
    print("%d file(s)%s" % (len(done), " (dry run, nothing changed)" if dry else ""))


if __name__ == "__main__":
    main()
