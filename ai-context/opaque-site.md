# Subject sites with opaque file names

A subject site can publish nothing but ciphertext and still say nothing about
what it holds. This page is the format and the rules. The code is
`tools/site/opaque.py` (names, pool, index), `tools/site/build_pool.py`
(writes the site), `tools/site/check_site.py` (the allowlist check) and, in
the browser, `js/pool-files.js`.

## What the repo holds

```
index.html      the hub loader (group-ui README); names nothing
boot.json       theme (sign-in look); nothing else
.itemrc    exact item UI tag, one line (written by build_pool.py)
.grouprc   hub UI version, one line, "3" = newest 3.x.x (set by hand or by
                the release action of the group template)
index.enc       encrypted index: subject settings + every book
r.html q.html   reader and quiz pages, opened as r.html#b=<book id>
.theme/         sign-in pictures and styles (no book names)
xx/<30 hex>.bin the pool: one flat set of files, 256 shards at most
.github/workflows/  check-site.yml, fork-warning.yml, no book names
```

No per-book folders, no `subject.json`, no `books.json`, no `book.json`.

## Names

```
name_key = HKDF-SHA256(subject key, info "rq/names/1")
id       = HMAC-SHA256(name_key, book id | path | flags | SHA-256(plain))[:32 hex]
file     = id[:2] / id[2:] + ".bin"
```

- The id depends on the content, so an unchanged file keeps its name and is
  not written again. A changed file gets a new name and the old one is swept.
- The id key is separate from the encryption key (HKDF), so names reveal
  nothing about the key. The files themselves are RQE1 (`encfmt.py`),
  encrypted with the subject key, per file. Text is gzipped, pictures are not.
- Test vector (guards the derivation; changing it orphans every site):
  key `00 01 ... 1f`, book `b`, path `p`, flags 1, content `x` gives
  `7f2197da7c421e9e97670eb395289b2c`.

## index.enc

RQE1, gzip. `{"format": 2, "subject": {...}, "books": [{"id", "title",
"summary", "cover", "views", "files": {"data/manifest.enc": "ab/cd....bin", ...}}]}`.
`files` maps a book's own file name to its pool path, so the readers and the
quiz keep asking for `data/manifest.enc`, `data/ch/<prefix>.enc`,
`media/<id>.enc`, `quiz/questions.enc` and `quiz/media/<id>.enc`;
`RecallQuizPool.addressOf(path)` turns that into the pool address. A reader
that finds a `format` it does not know stops; a new format is a new major.

### Split question banks

A book with more than one unit (chapter) of questions is published in
chunks instead of one `quiz/questions.enc` (`tools/site/split_questions.py`,
called by `build_pool.py`; the book repo still holds one `questions.json`):

- one pool file per unit, `quiz/q/<unit slug>.enc`; a unit over 300 questions
  becomes `<slug>-1`, `<slug>-2`...; questions without a unit go to `unsorted`;
- the name comes from the unit, not its position, so editing one chapter
  changes one pool file (the pool id hashes path and content);
- the first chunk also carries `id`, `book`, `labels` and every other key of
  the bank; questions are grouped by unit in order of first appearance;
- the book's index entry gets `"questionParts": ["quiz/q/...", ...]` in book
  order and no `quiz/questions.enc` in `files`. A bank that fits one chunk is
  stored as `quiz/questions.enc` as before;
- `js/early-questions.js` fetches the listed parts together and joins them
  (`mergeParts`); an entry without `questionParts` is read as before, so older
  sites keep working. Test: `tests/test_split_questions.py`.

## Publishing

The private book repo builds as before (`build_book.py`), then
`tools/site/publish_pool.sh` runs `build_pool.py` in the subject checkout:
it adds or replaces this book, keeps the others, sweeps files no book uses,
and commits with the message "Update". It prints counts only. Older
picture files (no header) are converted to RQE1 on the way.

## Checks

`check_site.py` runs in the subject repo (templates/check-site.yml) and
should be a required check. It fails on any top-level item off the
allowlist, a pool file that is not RQE1 or does not look random, an orphan
or missing file, a file not made with the subject key, and a book id, title,
subject title or ISBN-13 in a page, file name, workflow or commit message.
Its messages name the file, never the text found (the log of a public repo is
public). The ISBN check is for ISBN-13 only.

## Limits to remember

- The number of pool files, their sizes and when they change are still
  visible, and so is the repo's history if it was ever different.
- A file name in the pool is stable for the same content, so two sites with
  the same key and the same book would share names; use one key per subject.
- `index.enc` is replaced on every publish, so it is the one file whose
  history shows how often the subject changes.
