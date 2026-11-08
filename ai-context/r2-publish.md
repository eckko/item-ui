# Publishing a book to R2 (library)

The library keeps every book as ciphertext in a private Cloudflare R2
bucket. A Worker serves it and writes `index.json`. The publishing tool and
the workflow live in the private repo `eckko/actions` (`tools/publish_r2.py`,
`.github/workflows/refresh-library.yml`); it uses this repo's
`tools/book_build/build_book.py` and `tools/site/opaque.py` at an exact tag.
This page only describes what the reader must expect in the bucket.

## Layout in the bucket

```
p/<book id>/card.enc               title, author, tags, counts, version
p/<book id>/cover.enc              ~200x300 webp (needs Pillow), optional
p/<book id>/data/manifest.enc      the reader's own paths, unchanged:
p/<book id>/data/ch/<prefix>.enc   data/, media/, quiz/ as build_book.py
p/<book id>/media/<id>.enc         writes them
p/<book id>/quiz/questions.enc     one bank, or
p/<book id>/quiz/q/<unit>.enc      one file per unit, listed in
p/<book id>/quiz/parts.enc         a JSON list of the part paths
```

The book id is 32 hex characters made on the first refresh and kept, with the
book's last commit and tool tag, in a sealed `state/` object in the bucket
(never in a book repo, never served by the Worker). It never changes. Every file is RQE1 under the library key (`opaque.seal`).
`card.enc` and `index.json` are public; they hold ciphertext only.

## Change check and order

Each object carries the metadata `ph`, the content id of `opaque.file_id`
(HMAC of book id, path, flags and plain content). Same `ph`, no upload. The
upload order is chapters, pictures and quiz files, then `data/manifest.enc`,
`cover.enc`, `card.enc`. Objects of the book that the new set does not name
are deleted. `added` in the card is kept from the first publish.

## Worker paths

The Worker serves only `p/<id>/card.enc`, `p/<id>/cover.enc` and
`p/<id>/(data|media|quiz)/...enc` (up to three folders deep, no segment
starting with a dot). A new file kind needs the Worker's allow-list widened
first (`worker/src/index.js` in the library repo).

## How the reader and quiz find the files

A page that is not next to the book's files sets two meta tags:

```
<meta name="book-base" content="https://<worker>/p/<book id>/">
<meta name="book-version" content="<card.v>">
```

`js/book-files.js` turns a path into `<book-base><path>?v=<version>`; the
reader (`reader/app.js`), the question download (`js/early-questions.js`)
and the quiz pictures (`js/media-store.js`) all use it. Without the tags
the paths are unchanged, so every other site works as before.

A quiz page may also set `RecallQuizEncrypted.parts = "quiz/parts.enc"`:
the file is a JSON list of part paths, fetched together and joined. When
it cannot be read, `RecallQuizEncrypted.questions` (one bank) is used.
The key comes from `js/unlock.js` with `<meta name="key-doc"
content="library">`, so a reader signed in to the library on the same
site is not asked again.
