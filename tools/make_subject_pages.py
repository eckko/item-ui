#!/usr/bin/env python3
"""Write the three small pages of an encrypted book site (template v3).

    python3 tools/make_subject_pages.py 3.0.0 OUT_DIR --title "My Book"

OUT_DIR gets index.html, read.html and quiz.html. Each one only loads
js/unlock.js from one tagged copy of this template on jsDelivr; once the
reader has signed in (or pasted the subject key) it starts the view:

    index.html?view=read   the reader (reader/app.js, reader/style.css)
    index.html?view=quiz   the quiz (js/boot.js, as for a plain book)
    index.html             two buttons to choose

The questions (quiz/questions.enc) and the quiz pictures (quiz/media/)
stay in the book's own folder, encrypted with the subject key.

Use an exact tag such as 3.0.0 (no "v", never a branch).
"""

import argparse
import html
import re
import sys
from pathlib import Path

CDN_FOLDER = "https://cdn.jsdelivr.net/gh/eckko/item-ui"
TAG_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
QUESTIONS_FILE = "quiz/questions.enc"

HEAD = """<!doctype html>
<!-- Made by tools/make_subject_pages.py (item-ui {tag}).
     Loads everything from the CDN at that tag; to upgrade, run it again. -->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>{extra}
<script src="{base}/js/unlock.js"></script>
</head>
<body>
"""

FAILURE = """function showProblem(problem) {{
  var note = document.createElement("p");
  note.setAttribute("role", "alert");
  note.textContent = "Could not start: " + problem.message;
  document.body.appendChild(note);
}}"""

GATE_BODY = """<main id="choose" hidden>
<h1>{title}</h1>
<p>{buttons}</p>
</main>
<script>
SubjectUnlock.ready().then(function route() {{
  var view = new URLSearchParams(location.search).get("view");
  if (view === "read" || view === "quiz") {{
    location.replace(view + ".html");
    return;
  }}
  document.getElementById("choose").hidden = false;
}}).catch(showProblem);
{failure}
</script>
</body>
</html>
"""

GATE_STYLE = """
<style>
main{{max-width:26rem;margin:12vh auto;padding:0 1rem;font:1rem/1.5 system-ui,
sans-serif}}.go{{display:inline-block;margin:.25rem;padding:.6rem 1rem;
border:1px solid #2f5bd0;border-radius:.5rem;color:#2f5bd0;
text-decoration:none;font-weight:600}}.go:first-child{{background:#2f5bd0;
color:#fff}}
</style>"""

READ_BODY = """<script>
SubjectUnlock.ready().then(function start() {{
  return SubjectUnlock.loadScript("{base}/reader/app.js");
}}).catch(showProblem);
{failure}
</script>
</body>
</html>
"""

QUIZ_BODY = """<script>
SubjectUnlock.ready().then(function start() {{
  return SubjectUnlock.loadScript("{base}/js/boot.js");
}}).catch(showProblem);
{failure}
</script>
</body>
</html>
"""


def cdn_base(tag):
    """The CDN folder holding one tag of the template (no trailing /)."""
    return f"{CDN_FOLDER}@{tag}"


BUTTONS = {
    "read": '<a class="go" href="read.html">Read book</a>',
    "quiz": '<a class="go" href="quiz.html">Test only</a>',
}


def make_pages(tag, title, views=("read", "quiz")):
    """The pages as {file name: text}: index.html and one per view.

    @param views: which views the book has, "read" and/or "quiz"
    """
    base = cdn_base(tag)
    name = html.escape(title)
    failure = FAILURE.format()

    def head(extra=""):
        return HEAD.format(tag=tag, base=base, title=name, extra=extra)

    buttons = "\n".join(BUTTONS[view] for view in ("read", "quiz")
                        if view in views)
    pages = {"index.html": head(GATE_STYLE.format()) + GATE_BODY.format(
        title=name, buttons=buttons, failure=failure)}
    if "read" in views:
        pages["read.html"] = head(
            f'\n<link rel="stylesheet" href="{base}/reader/style.css">'
        ) + READ_BODY.format(base=base, failure=failure)
    if "quiz" in views:
        pages["quiz.html"] = head(
            "\n<script>window.RecallQuizEncrypted = "
            '{ questions: "' + QUESTIONS_FILE + '" };</script>'
        ) + QUIZ_BODY.format(base=base, failure=failure)
    return pages


POOL_PAGES = {"read": "r.html", "quiz": "q.html"}
POOL_META = '\n<meta name="pool-index" content="index.enc">'
POOL_SCRIPT = '\n<script src="{base}/js/pool-files.js"></script>'


def make_pool_pages(tag):
    """The two pages of a site whose files all sit under opaque names:
    r.html (reader) and q.html (quiz), opened as r.html#b=<book id>.

    They carry no title and no book name; the reader and the quiz set
    their own title once the reader has signed in and the encrypted index
    (index.enc) has been read (js/pool-files.js). There is no gate page:
    the hub (index.html) is the shelf.

    @param tag: exact template tag, for example 3.4.0
    @return: {file name: text}
    """
    base = cdn_base(tag)
    failure = FAILURE.format()

    def head(extra):
        text = HEAD.format(tag=tag, base=base, title="Loading", extra=extra)
        return text.replace("</script>\n</head>", "</script>" +
                            POOL_SCRIPT.format(base=base) + "\n</head>", 1)

    return {
        POOL_PAGES["read"]: head(
            POOL_META + f'\n<link rel="stylesheet" href="{base}/reader/'
            'style.css">') + READ_BODY.format(base=base, failure=failure),
        POOL_PAGES["quiz"]: head(
            POOL_META + "\n<script>window.RecallQuizEncrypted = "
            '{ questions: "' + QUESTIONS_FILE + '" };</script>'
        ) + QUIZ_BODY.format(base=base, failure=failure),
    }


def main():
    """Write the pages."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("tag", help="exact template tag, for example 3.0.0")
    parser.add_argument("out", type=Path, help="the book's folder")
    parser.add_argument("--title", default="Book")
    args = parser.parse_args()
    if not TAG_PATTERN.match(args.tag):
        sys.exit('the tag must look like 3.0.0 (no "v", no branch)')
    args.out.mkdir(parents=True, exist_ok=True)
    for file_name, text in make_pages(args.tag, args.title).items():
        (args.out / file_name).write_text(text, encoding="utf-8")
        print("wrote", args.out / file_name)


if __name__ == "__main__":
    main()
