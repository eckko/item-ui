#!/usr/bin/env python3
"""Tests for tools/book_build/epubnav.py. Run: python3 tests/test_epubnav.py"""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "book_build"))
import epubnav  # noqa: E402

OPF_NAV = ('<package><manifest>'
           '<item id="nav" href="text/nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           '<item id="c1" href="text/c1.xhtml" media-type="application/xhtml+xml"/>'
           '</manifest></package>')
NAV = ('<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
       '<nav epub:type="landmarks"><ol><li><a href="cover.xhtml">Cover</a></li></ol></nav>'
       '<nav epub:type="toc"><ol>'
       '<li><a href="c1.xhtml">Chapter 1 - Habits &amp; Cues</a>'
       '<ol><li><a href="c1.xhtml#s1">A <i>first</i>\n section</a></li></ol></li>'
       '<li><a href="../extra/c2.xhtml">Chapter 2</a></li>'
       '<li><a href="https://example.com/x">Outside</a></li>'
       '</ol></nav></body></html>')


def entries(ncx):
    return re.findall(r"<navPoint[^>]*>.*?<text>(.*?)</text>.*?"
                      r'<content src="([^"#]+)', ncx, re.S)


class Nav(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "text").mkdir()

    def test_nav_document_is_used_when_there_is_no_ncx(self):
        (self.tmp / "text" / "nav.xhtml").write_text(NAV, encoding="utf8")
        ncx = epubnav.read_ncx(self.tmp, OPF_NAV)
        self.assertEqual(entries(ncx), [
            ("Chapter 1 - Habits &amp; Cues", "text/c1.xhtml"),
            ("A first section", "text/c1.xhtml"),
            ("Chapter 2", "extra/c2.xhtml")])

    def test_real_ncx_still_wins(self):
        (self.tmp / "toc.ncx").write_text("<ncx>real</ncx>", encoding="utf8")
        (self.tmp / "text" / "nav.xhtml").write_text(NAV, encoding="utf8")
        self.assertEqual(epubnav.read_ncx(self.tmp, OPF_NAV), "<ncx>real</ncx>")

    def test_nothing_usable_stops_with_a_message(self):
        with self.assertRaises(SystemExit) as e:
            epubnav.read_ncx(self.tmp, "<package><manifest/></package>")
        self.assertIn("nav.xhtml", str(e.exception))


if __name__ == "__main__":
    unittest.main()
