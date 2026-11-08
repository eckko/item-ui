# Publishing a book to R2 (one-ui)

The one-ui library keeps every book as ciphertext in a private Cloudflare R2
bucket. A Worker serves it and writes `index.json`. This page is the part
that lives in this repo: `tools/site/publish_r2.py`, the workflow
`templates/publish-r2.yml` and the tests `tests/test_publish_r2.py`.

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

The book id is 32 hex characters from `.onebook.json` in the book repo. It
never changes. Every file is RQE1 under the library key (`opaque.seal`).
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
first (`worker/src/index.js` in the one-ui repo).

## Not done yet

The reader and quiz still read their files from the page's own folder or the
pool. Reading from `<meta name="book-base">`, adding `?v=<card.v>` and loading
`quiz/parts.enc` is phase 6 of the one-ui plan.
