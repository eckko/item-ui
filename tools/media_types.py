"""Media types and the file extension of each (the plain files of a book).

A question's media entry {id, type} is the file media/<id>.<ext> in the book
repo. js/media-store.js has the same table (EXTENSIONS); tests/test_plain_media.py
checks that the two agree.
"""

DEFAULT_TYPE = "image/webp"

EXTENSIONS = {
    "image/webp": "webp", "image/png": "png", "image/jpeg": "jpg",
    "image/gif": "gif", "image/svg+xml": "svg", "image/avif": "avif",
    "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/ogg": "ogg",
    "audio/wav": "wav", "video/mp4": "mp4", "video/webm": "webm",
}
TYPES = {ext: kind for kind, ext in EXTENSIONS.items()}


def extension_of(kind):
    """The file extension for a media type (none given means webp)."""
    return EXTENSIONS.get(kind or DEFAULT_TYPE)


def entries(bank):
    """Every media entry {id, type...} of a question bank, in any card,
    question or block, and the bank's cover."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "media" and isinstance(value, list):
                    found.extend(v for v in value if isinstance(v, dict))
                elif key == "cover" and isinstance(value, dict):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(bank)
    return [e for e in found if e.get("id")]
