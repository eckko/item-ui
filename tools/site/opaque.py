"""Opaque file names for an encrypted subject site.

A subject site that publishes only ciphertext should also not say what the
ciphertext is. This module keeps every published file in one flat pool:

    bin/xx/<rest>.bin  xx = the first two characters of the file id

(Before the pool moved under bin/, the xx folders sat at the top of the
site; move_pool_into_bin() moves such a pool and points index.enc at it.)

The id is derived from a secret, so the name tells nobody which book or
which part of a book a file belongs to. Only the encrypted index
(index.enc) maps a book's own file names to ids.

    id = HMAC-SHA256(name_key, book id | path | flags | SHA-256 of content)

The id depends on the content, so a file that did not change keeps its id
and is not written again (encryption uses a fresh nonce each time, so the
ids are what tell us a file is the same). A file that changed gets a new
id, and the old one becomes an orphan that sweep() removes.

Keys. The subject key encrypts every file, as before (RQE1, see
tools/book_build/encfmt.py). The id uses a separate key from HKDF, so the
ids reveal nothing about the encryption key.

This module has no knowledge of any particular book.
"""

import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"RQE1"
KEY_ID = "s001"
HEADER_BYTES = 9
NONCE_BYTES = 12
TAG_BYTES = 16
MIN_FILE_BYTES = HEADER_BYTES + NONCE_BYTES + TAG_BYTES
ID_CHARS = 32
POOL_EXTENSION = ".bin"
POOL_FOLDER = "bin"              # every pool file lives under this folder
INDEX_NAME = "index.enc"
BOOT_NAME = "boot.json"
ITEM_VERSION_NAME = ".itemrc"    # exact tag of the item UI (reader, quiz, tools)
GROUP_VERSION_NAME = ".grouprc"  # which hub UI to load ("3" = newest 3.x.x)
INDEX_FORMAT = 2
REF_PATTERN = re.compile(r"^bin/[0-9a-f]{2}/[0-9a-f]{%d}\.bin$"
                         % (ID_CHARS - 2))
# the path of a pool file before the pool moved under bin/
OLD_REF_PATTERN = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{%d}\.bin$"
                             % (ID_CHARS - 2))


def parse_key(text):
    """The 32 key bytes from hex or base64 text.

    @param text: the contents of a key file
    @return: 32 bytes
    """
    import base64
    text = text.strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return bytes.fromhex(text)
    raw = base64.b64decode(text + "=" * (-len(text) % 4))
    if len(raw) != 32:
        raise ValueError("the key must be 32 bytes (64 hex characters "
                         "or base64)")
    return raw


def subkey(key, purpose):
    """A key for one purpose, from the subject key (HKDF-SHA-256).

    @param key: the 32-byte subject key
    @param purpose: a short text such as "names"
    @return: 32 bytes
    """
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=("rq/" + purpose + "/1").encode()).derive(key)


def seal(key, data, compress):
    """The RQE1 bytes of one file.

    @param key: the 32-byte subject key
    @param data: the plain bytes
    @param compress: gzip before encrypting (for text, not pictures)
    @return: bytes to store
    """
    if compress:
        data = gzip.compress(data, 9, mtime=0)
    head = MAGIC + bytes([1 if compress else 0]) + KEY_ID.encode("ascii")
    nonce = os.urandom(NONCE_BYTES)
    return head + nonce + AESGCM(key).encrypt(nonce, data, head)


def unseal(key, blob):
    """The plain bytes of an RQE1 file.

    @param key: the 32-byte subject key
    @param blob: the stored bytes
    @return: the plain bytes
    """
    head, nonce, body = (blob[:HEADER_BYTES],
                         blob[HEADER_BYTES:HEADER_BYTES + NONCE_BYTES],
                         blob[HEADER_BYTES + NONCE_BYTES:])
    if head[:4] != MAGIC:
        raise ValueError("not an RQE1 file")
    data = AESGCM(key).decrypt(nonce, body, head)
    return gzip.decompress(data) if head[4] & 1 else data


def file_id(name_key, book_id, path, flags, content):
    """The opaque id of one file.

    @param name_key: subkey(key, "names")
    @param book_id: the book the file belongs to
    @param path: the file's own name inside the book, for example
        "data/manifest.enc"
    @param flags: 1 when the file is compressed before encryption
    @param content: the plain bytes
    @return: ID_CHARS lowercase hex characters
    """
    digest = hashlib.sha256(content).hexdigest()
    message = "\0".join([book_id, path, str(flags), digest]).encode("utf-8")
    return hmac.new(name_key, message, hashlib.sha256).hexdigest()[:ID_CHARS]


def ref_of(identifier):
    """The pool path of an id: 'bin/ab/cdef....bin'."""
    return (POOL_FOLDER + "/" + identifier[:2] + "/" + identifier[2:]
            + POOL_EXTENSION)


class Pool:
    """The flat folder of encrypted files, with the ids that are in use."""

    def __init__(self, root, key):
        """
        @param root: the pool folder (the site root)
        @param key: the 32-byte subject key
        """
        self.root = Path(root)
        self.key = key
        self.name_key = subkey(key, "names")
        self.used = set()
        self.written = 0
        self.skipped = 0

    def put(self, book_id, path, content, compress=True):
        """Store one file unless it is already there.

        @param book_id: the owning book
        @param path: the file's own name inside the book
        @param content: the plain bytes
        @param compress: gzip before encrypting (text yes, pictures no)
        @return: the pool path ('bin/ab/cdef....bin')
        """
        flags = 1 if compress else 0
        ref = ref_of(file_id(self.name_key, book_id, path, flags, content))
        self.used.add(ref)
        target = self.root / ref
        if target.is_file() and target.stat().st_size >= MIN_FILE_BYTES \
                and target.read_bytes()[:4] == MAGIC:
            self.skipped += 1
            return ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(seal(self.key, content, compress))
        self.written += 1
        return ref

    def orphans(self):
        """Pool files no put() asked for in this run.

        @return: sorted list of pool paths
        """
        found = []
        for item in self.root.glob(POOL_FOLDER + "/??/*" + POOL_EXTENSION):
            ref = item.relative_to(self.root).as_posix()
            if ref not in self.used:
                found.append(ref)
        return sorted(found)

    def sweep(self):
        """Delete the orphans and any shard folder left empty.

        Call this only after every book of the site went through put(),
        or files of the books you left out are deleted.

        @return: the pool paths removed
        """
        removed = self.orphans()
        for ref in removed:
            (self.root / ref).unlink()
        pool = self.root / POOL_FOLDER
        for folder in sorted(pool.glob("??")):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        if pool.is_dir() and not any(pool.iterdir()):
            pool.rmdir()
        return removed


def old_pool_files(root):
    """Pool files still in the old place: xx/<id>.bin at the top of a site.

    @param root: the site root
    @return: sorted list of their paths relative to root
    """
    root = Path(root)
    found = []
    for folder in sorted(root.glob("??")):
        if not folder.is_dir():
            continue
        for item in sorted(folder.glob("*" + POOL_EXTENSION)):
            ref = item.relative_to(root).as_posix()
            if OLD_REF_PATTERN.match(ref):
                found.append(ref)
    return found


def move_pool_into_bin(root, key):
    """Move an old pool (xx/<id>.bin at the top of the site) under bin/ and
    point every book of index.enc at the new paths.

    Safe to repeat: with the pool already under bin/ it does nothing. The
    index is read first, so a wrong key stops it before any file moves.

    @param root: the site root
    @param key: the subject key
    @return: the number of files moved
    """
    root = Path(root)
    old = old_pool_files(root)
    index = read_index(root, key) if (root / INDEX_NAME).is_file() else None
    changed = False
    for book in (index or {}).get("books", []):
        files = book.get("files", {})
        for logical, ref in list(files.items()):
            if OLD_REF_PATTERN.match(ref):
                files[logical] = POOL_FOLDER + "/" + ref
                changed = True
    for ref in old:
        target = root / POOL_FOLDER / ref
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(root / ref, target)
    for folder in sorted(root.glob("??")):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    if changed:
        write_index(root, key, index)
    return len(old)


def write_index(root, key, index):
    """Encrypt and write index.enc.

    @param root: the site root
    @param key: the subject key
    @param index: the dict (see build_index)
    """
    data = json.dumps(index, ensure_ascii=False, separators=(",", ":"))
    (Path(root) / INDEX_NAME).write_bytes(
        seal(key, data.encode("utf-8"), True))


def read_index(root, key):
    """The decrypted index of a site, for checks and tests."""
    return json.loads(unseal(key, (Path(root) / INDEX_NAME).read_bytes()))


def new_book_id():
    """A random id for a book's address: 20 hex characters (80 bits).

    The id only names the book inside index.enc and in the page address
    (r.html#b=<id>). It is not made from the book's name, so it tells
    nobody anything, and it never changes once the book has one.
    """
    return secrets.token_hex(10)


def build_index(subject, books):
    """The index document.

    @param subject: the subject's own settings (title and so on)
    @param books: list of {"id", "repo", "title", "summary", "cover",
        "views", "files"}; id is the random address id, repo the book's own
        name (the book repo and its status record); files maps a book's own file name to a pool path
    @return: dict
    """
    return {"format": INDEX_FORMAT, "subject": subject, "books": books}


def read_version(root, name):
    """One version file's value, or "" when it is missing.

    @param root: the site root
    @param name: ITEM_VERSION_NAME or GROUP_VERSION_NAME
    @return: the first line, stripped
    """
    path = Path(root) / name
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return lines[0].strip() if lines else ""


def write_version(root, name, value):
    """Write a version file: the value and a newline, nothing else."""
    (Path(root) / name).write_text(str(value).strip() + "\n", encoding="utf-8")


def write_boot(root, theme=None):
    """Write boot.json, the only readable settings file. The two versions
    live in their own files (.itemrc, .grouprc).

    @param root: the site root
    @param theme: the sign-in look (the subject.json "theme" object), or
        None; it names colours and art, never a book
    """
    boot = {}
    if theme:
        boot["theme"] = theme
    text = json.dumps(boot, indent=2) + "\n"
    (Path(root) / BOOT_NAME).write_text(text, encoding="utf-8")
