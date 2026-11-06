#!/usr/bin/env python3
"""Check that a subject site publishes nothing readable.

    python3 check_site.py [SITE] [--key-env SUBJECT_KEY] [--git-log]
                          [--terms FILE]

Problems name the file, never the text found in it (a public repo's
log is public); the hidden names are numbered in sorted order.
Exit status 1 (and a list of problems) when the site would show anything
but ciphertext. Run it in the subject repo's pull requests and on every push,
and make it a required check, so a bad file cannot be merged.

Always checked (no key needed):
  * only these top-level items exist: index.html, boot.json, index.enc,
    r.html, q.html, .itemrc, .grouprc, .nojekyll, CNAME, .gitignore,
    README.md, CLAUDE.md, .github/workflows/*.yml,
    .theme/ (pictures and styles for the sign-in page only) and the
    two-character folders of the pool;
  * a pool folder holds only <30 hex characters>.bin files, each an RQE1
    file that looks like random data (no readable text inside);
  * boot.json holds only theme (an older site may still hold templateTag
    and uiVersion there); the versions are in .itemrc and .grouprc.

With the subject key (environment variable named by --key-env, 64 hex
characters or base64) it also:
  * decrypts index.enc and every pool file, so a file that is not made with
    the subject key fails;
  * requires every file of every book to exist and no pool file to be
    unlisted (no orphans);
  * looks in every readable file (pages, boot.json, theme files, workflows,
    file names) for a book id, a book title or the subject title from the
    index, for ISBN-13 numbers, and for each line of --terms FILE.
    With --git-log it checks commit messages the same way.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import opaque  # noqa: E402

ROOT_FILES = {"index.html", "boot.json", "index.enc", "r.html", "q.html",
              ".itemrc", ".grouprc", ".nojekyll", "CNAME", ".gitignore",
              "README.md", "CLAUDE.md"}
ITEM_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
GROUP_VERSION = re.compile(r"^\d+(\.\d+){0,2}$")
ROOT_FOLDERS = {".github", ".theme", ".git"}
BOOT_KEYS = {"templateTag", "uiVersion", "theme"}
THEME_SUFFIXES = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".css",
                  ".woff", ".woff2", ".ico", ".txt"}
SHARD = re.compile(r"^[0-9a-f]{2}$")
POOL_NAME = re.compile(r"^[0-9a-f]{%d}\.bin$" % (opaque.ID_CHARS - 2))
ISBN13 = re.compile(r"(?<!\d)97[89][- ]?\d(?:[- ]?\d){9}(?!\d)")
MIN_ENTROPY = 7.2            # bits per byte; ciphertext is about 7.99
ENTROPY_FROM_BYTES = 512     # shorter files are too small to measure
READABLE_SUFFIXES = {".html", ".json", ".yml", ".yaml", ".md", ".txt",
                     ".css", ".svg", ".js"}


def entropy(data):
    """Shannon entropy of the bytes, in bits per byte."""
    if not data:
        return 0.0
    total = len(data)
    return -sum(n / total * math.log2(n / total)
                for n in Counter(data).values())


def check_layout(site):
    """The allowlist, the pool names and boot.json (no key needed).

    @param site: the site folder
    @return: list of problems
    """
    problems = []
    for item in sorted(site.iterdir()):
        name = item.name
        if item.is_file() and name in ROOT_FILES:
            continue
        if item.is_dir() and (name in ROOT_FOLDERS or SHARD.match(name)):
            continue
        problems.append("%s is not allowed at the top of the site" % name)
    workflows = site / ".github"
    for item in sorted(workflows.rglob("*")) if workflows.is_dir() else []:
        rel = item.relative_to(site).as_posix()
        if item.is_file() and not (
                item.parent == workflows / "workflows"
                and item.suffix in (".yml", ".yaml")):
            problems.append("%s is not a workflow file" % rel)
    theme = site / ".theme"
    for item in sorted(theme.rglob("*")) if theme.is_dir() else []:
        if item.is_file() and item.suffix.lower() not in THEME_SUFFIXES:
            problems.append("%s is not a picture or style" %
                            item.relative_to(site).as_posix())
    for folder in sorted(site.iterdir()):
        if not (folder.is_dir() and SHARD.match(folder.name)):
            continue
        for item in sorted(folder.iterdir()):
            rel = item.relative_to(site).as_posix()
            if not (item.is_file() and POOL_NAME.match(item.name)):
                problems.append("%s is not a pool file name" % rel)
                continue
            problems += check_pool_file(item, rel)
    boot = site / "boot.json"
    if boot.is_file():
        try:
            extra = set(json.loads(boot.read_text("utf-8"))) - BOOT_KEYS
        except ValueError as error:
            problems.append("boot.json cannot be read: %s" % error)
        else:
            if extra:
                problems.append("boot.json may hold only %s, not %s" % (
                    ", ".join(sorted(BOOT_KEYS)), ", ".join(sorted(extra))))
    else:
        problems.append("boot.json is missing")
    for name, pattern, what in (
            (opaque.ITEM_VERSION_NAME, ITEM_VERSION, "an exact tag such as 3.4.0"),
            (opaque.GROUP_VERSION_NAME, GROUP_VERSION, "a version such as 3 or 3.1.0")):
        value = opaque.read_version(site, name)
        if (site / name).is_file() and not pattern.match(value):
            problems.append("%s must hold %s" % (name, what))
    if (site / opaque.GROUP_VERSION_NAME).is_file() and not (site / ".nojekyll").is_file():
        problems.append(".nojekyll is missing: GitHub Pages would hide .grouprc "
                        "and .itemrc, and the hub could not start")
    legacy_tag = False
    if boot.is_file():
        try:
            legacy_tag = "templateTag" in json.loads(boot.read_text("utf-8"))
        except ValueError:
            pass
    if not (site / opaque.ITEM_VERSION_NAME).is_file() and not legacy_tag:
        problems.append(".itemrc is missing")
    if not (site / opaque.INDEX_NAME).is_file():
        problems.append("index.enc is missing")
    else:
        problems += check_pool_file(site / opaque.INDEX_NAME, "index.enc")
    return problems


def check_pool_file(path, rel):
    """An RQE1 file that looks like random data.

    @param path: the file
    @param rel: its name for messages
    @return: list of problems
    """
    data = path.read_bytes()
    if len(data) < opaque.MIN_FILE_BYTES or data[:4] != opaque.MAGIC:
        return ["%s is not an encrypted (RQE1) file" % rel]
    body = data[opaque.HEADER_BYTES:]
    if len(body) >= ENTROPY_FROM_BYTES and entropy(body) < MIN_ENTROPY:
        return ["%s does not look encrypted (readable content)" % rel]
    return []


def read_key(env_name):
    """The subject key from the environment, or None."""
    text = os.environ.get(env_name, "").strip()
    return opaque.parse_key(text) if text else None


def names_to_hide(index, extra_terms):
    """Every text that must not appear in a public file.

    @param index: the decrypted index
    @param extra_terms: more texts
    @return: list of lowercase strings
    """
    names = [index.get("subject", {}).get("title", "")]
    for book in index.get("books", []):
        names += [book.get("id", ""), book.get("repo", ""),
                  book.get("title", "")]
    names += list(extra_terms)
    return sorted({n.strip().lower() for n in names if len(n.strip()) >= 3})


def readable_files(site):
    """The files a visitor can read, as (name for messages, text)."""
    for item in sorted(site.rglob("*")):
        rel = item.relative_to(site)
        if not item.is_file() or rel.parts[0] == ".git":
            continue
        if SHARD.match(rel.parts[0]) or rel.as_posix() == opaque.INDEX_NAME:
            continue
        if item.suffix.lower() in READABLE_SUFFIXES or item.name in (
                "CNAME", ".gitignore"):
            yield rel.as_posix(), item.read_text("utf-8", errors="replace")


def slug(text):
    """Lowercase letters and digits, runs of anything else as one "-", so
    "My Subject", "my_subject" and "my-subject.svg" compare equal."""
    return "-%s-" % re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def find_leaks(text, label, hidden):
    """Problems for one text: a hidden name or an ISBN-like number."""
    problems = []
    slugged = slug(text)
    for number, name in enumerate(hidden, 1):
        if slug(name) in slugged:
            # never print the name: the log of a public repo is public
            problems.append("%s contains hidden name #%d" % (label, number))
    if ISBN13.search(text):
        problems.append("%s contains an ISBN-like number" % label)
    return problems


def check_with_key(site, key, extra_terms, git_log):
    """Everything that needs the key.

    @return: list of problems
    """
    problems = []
    try:
        index = opaque.read_index(site, key)
    except Exception as error:  # wrong key or a damaged file
        return ["index.enc cannot be decrypted with the key (%s)" %
                type(error).__name__]
    if index.get("format") != opaque.INDEX_FORMAT:
        problems.append("index.enc has an unknown format")
    listed = set()
    for book in index.get("books", []):
        for logical, ref in sorted(book.get("files", {}).items()):
            if not opaque.REF_PATTERN.match(ref):
                problems.append("book file %s has a bad pool name" % logical)
                continue
            listed.add(ref)
            target = site / ref
            if not target.is_file():
                problems.append("%s is listed but missing" % ref)
                continue
            try:
                opaque.unseal(key, target.read_bytes())
            except Exception:
                problems.append("%s is not made with the subject key" % ref)
    on_disk = {p.relative_to(site).as_posix() for p in site.glob("??/*.bin")}
    for ref in sorted(on_disk - listed):
        problems.append("%s is not in the index (orphan)" % ref)
    hidden = names_to_hide(index, extra_terms)
    for label, text in readable_files(site):
        problems += find_leaks(text, label, hidden)
    for item in sorted(site.rglob("*")):
        rel = item.relative_to(site).as_posix()
        if not rel.startswith(".git/") and not SHARD.match(rel.split("/")[0]):
            problems += find_leaks(rel, "the name " + rel, hidden)
    if git_log:
        log = subprocess.run(["git", "-C", str(site), "log", "--format=%B"],
                             capture_output=True, text=True).stdout
        problems += find_leaks(log, "a commit message", hidden)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("site", nargs="?", default=".")
    parser.add_argument("--key-env", default="SUBJECT_KEY")
    parser.add_argument("--git-log", action="store_true")
    parser.add_argument("--terms", help="file with one forbidden text per line")
    args = parser.parse_args(argv)
    site = Path(args.site)
    problems = check_layout(site)
    extra = []
    if args.terms:
        extra = Path(args.terms).read_text("utf-8").splitlines()
    key = read_key(args.key_env)
    if key is None:
        print("note: %s is not set, so the key checks are skipped"
              % args.key_env)
    elif (site / opaque.INDEX_NAME).is_file():
        problems += check_with_key(site, key, extra, args.git_log)
    for problem in problems:
        print("PROBLEM:", problem)
    print("%d problem(s)" % len(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
