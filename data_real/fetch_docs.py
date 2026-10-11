"""
data_real/fetch_docs.py

Downloads the PUBLIC SLA pages listed in manifest.json into data_real/cache/ as plain text
(table rows become "cell | cell" lines). The cache is git-ignored: the pages belong to their
publishers, so the repo stores only links, never copies.

    python -m data_real.fetch_docs
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
HEADERS = {"User-Agent": "Mozilla/5.0 (research; SLA-harness student project)"}
BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "tr", "table", "ul", "ol"}
SKIP = {"script", "style", "noscript", "nav", "footer", "header", "svg", "form", "button"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.row = [], 0, None

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.row.append("")
        elif tag in BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1
        elif tag == "tr" and self.row is not None:
            cells = [re.sub(r"\s+", " ", c).strip() for c in self.row if c.strip()]
            if cells:
                self.out.append("\n" + " | ".join(cells) + "\n")
            self.row = None
        elif tag in BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self.skip:
            return
        if self.row is not None and self.row:
            self.row[-1] += data
        elif self.row is None:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = "".join(p.out)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def main() -> int:
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    CACHE.mkdir(exist_ok=True)
    failed = 0
    for doc in manifest["documents"]:
        out = CACHE / f"{doc['id']}.txt"
        try:
            r = requests.get(doc["url"], headers=HEADERS, timeout=30)
            r.raise_for_status()
            text = html_to_text(r.content.decode("utf-8", errors="replace"))
            out.write_text(text, encoding="utf-8")
            print(f"ok    {doc['id']:<14} {len(text):>7,} chars  <- {doc['url']}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {doc['id']:<14} {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
