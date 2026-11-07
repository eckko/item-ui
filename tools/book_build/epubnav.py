"""Small helpers shared by the EPUB build tools."""
import re
from pathlib import Path

NCX_TYPE = "application/x-dtbncx+xml"


def read_ncx(opf_dir, opf_text):
    """The navigation (NCX) text of an unzipped EPUB.

    Uses toc.ncx next to the package file when there is one, otherwise the
    NCX file the package declares (some EPUBs name it <isbn>_toc.ncx)."""
    plain = Path(opf_dir) / "toc.ncx"
    if plain.exists():
        return plain.read_text(encoding="utf8")
    for item in re.findall(r"<item\b[^>]*>", opf_text):
        if NCX_TYPE in item:
            href = re.search(r'href="([^"]+)"', item)
            declared = Path(opf_dir) / href.group(1) if href else None
            if declared and declared.exists():
                return declared.read_text(encoding="utf8")
    nav = nav_document_as_ncx(opf_dir, opf_text)
    if nav:
        return nav
    raise SystemExit("no navigation file: the EPUB has no toc.ncx, its "
                     "package file declares no NCX and no EPUB 3 navigation "
                     "document (nav.xhtml)")


def nav_document_as_ncx(opf_dir, opf_text):
    """The EPUB 3 navigation document (the manifest item with
    properties="nav") written as NCX navPoints, in reading order, so the
    callers that read NCX work on an EPUB that has only nav.xhtml. Each href
    is made relative to the package folder, as an NCX src would be. Returns
    "" when there is no usable navigation document."""
    import posixpath
    from html import escape
    from bs4 import BeautifulSoup
    for item in re.findall(r"<item\b[^>]*>", opf_text):
        props = re.search(r'properties="([^"]*)"', item)
        if not props or "nav" not in props.group(1).split():
            continue
        href = re.search(r'href="([^"]+)"', item)
        if not href:
            continue
        path = Path(opf_dir) / href.group(1)
        if not path.exists():
            continue
        soup = BeautifulSoup(path.read_text(encoding="utf8"), "html.parser")
        toc = next((n for n in soup.find_all("nav")
                    if "toc" in (n.get("epub:type") or n.get("type") or "")),
                   soup.find("nav"))
        if toc is None:
            continue
        folder = posixpath.dirname(href.group(1))
        points = []
        for a in toc.find_all("a", href=True):
            title = re.sub(r"\s+", " ", a.get_text(" ")).strip()
            target = a["href"]
            if not title or re.match(r"[a-z]+:", target):
                continue
            src = posixpath.normpath(posixpath.join(folder, target.split("#")[0]))
            if "#" in target:
                src += "#" + target.split("#", 1)[1]
            points.append("<navPoint><navLabel><text>%s</text></navLabel>"
                          '<content src="%s"/></navPoint>'
                          % (escape(title, quote=False), escape(src)))
        if points:
            return "<ncx><navMap>%s</navMap></ncx>" % "".join(points)
    return ""


def first_word_key(text):
    """Lower-case first part of a title before a colon, for matching an
    EPUB navigation entry ("Foreword: to the 50th ...") with a section
    title ("Foreword to the 50th Anniversary Edition: ...")."""
    text = text.replace("’", "'").replace("‘", "'")
    head = text.split(":")[0]
    return re.sub(r"[^a-z0-9 ]", "", head.lower()).strip()
