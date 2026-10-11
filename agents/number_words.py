"""
agents/number_words.py

Finds every number in a piece of text, whether written with digits ("99.5")
or in words ("ninety-nine point five", "twenty-four"). Deterministic and
dependency-free; used by the regex reader and by the quote verifier.
"""
from __future__ import annotations

import re
from typing import List, Tuple

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_DIGITS_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_WORD_RE = re.compile(r"[a-z]+")


def _words_value(tokens: List[str]) -> float:
    total, current = 0, 0
    for t in tokens:
        if t in _UNITS:
            current += _UNITS[t]
        elif t in _TENS:
            current += _TENS[t]
        elif t == "hundred":
            current = max(current, 1) * 100
    return float(total + current)


def find_numbers(text: str) -> List[Tuple[float, int, int]]:
    """Return (value, start, end) for each number in `text`, in order of position."""
    found: List[Tuple[float, int, int]] = []
    for m in _DIGITS_RE.finditer(text):
        found.append((float(m.group(0).replace(",", "")), m.start(), m.end()))

    lowered = text.lower()
    tokens = [(m.group(0), m.start(), m.end()) for m in _WORD_RE.finditer(lowered)]
    i = 0
    while i < len(tokens):
        word = tokens[i][0]
        if word in _UNITS or word in _TENS:
            j = i
            group = [word]
            # compounds: "twenty-four" tokenises as twenty, four (hyphen is not a letter)
            while j + 1 < len(tokens):
                nxt = tokens[j + 1][0]
                gap = lowered[tokens[j][2]:tokens[j + 1][1]]
                if nxt in _UNITS or nxt in _TENS or nxt == "hundred":
                    if gap.strip(" -") == "" and (
                        (group[-1] in _TENS and nxt in _UNITS and _UNITS[nxt] < 10)
                        or (nxt == "hundred")
                    ):
                        group.append(nxt)
                        j += 1
                        continue
                break
            value = _words_value(group)
            end = tokens[j][2]
            # "ninety-nine point five" / "ninety nine point five five"
            if j + 2 < len(tokens) and tokens[j + 1][0] == "point" and tokens[j + 2][0] in _UNITS:
                k = j + 2
                frac = ""
                while k < len(tokens) and tokens[k][0] in _UNITS and _UNITS[tokens[k][0]] < 10:
                    frac += str(_UNITS[tokens[k][0]])
                    end = tokens[k][2]
                    k += 1
                if frac:
                    value = float(f"{int(value)}.{frac}")
                    j = k - 1
            found.append((value, tokens[i][1], end))
            i = j + 1
        else:
            i += 1
    found.sort(key=lambda x: x[1])
    return found
