"""Cut a book's question bank into one chunk per unit (chapter).

Used by build_pool.py at publish time. The book repo keeps a single
questions.json; only the encrypted pool is split. A chunk is named after its
unit, not its position, so adding a question to one chapter changes only that
chapter's file (the pool names a file by a hash of its path and content).

  chunks(bank)        -> [(path, bank_chunk)]  in book order; [] when the bank
                         fits in one chunk (the caller then stores one file)
  merge(parts)        -> the bank again (what js/early-questions.js does)

Questions are grouped by their "unit" (first appearance order; original
order inside a unit). A unit with more than MAX_PER_CHUNK questions is cut
into numbered pieces. The first chunk also carries every key of the bank other
than "questions" (id, book, labels...).
"""

import json
import re

MAX_PER_CHUNK = 300
PREFIX = "quiz/q/"


def slug(unit):
    text = re.sub(r"[^a-z0-9]+", "-", str(unit or "").lower()).strip("-")
    return text[:40] or "unsorted"


def chunks(bank, max_per_chunk=MAX_PER_CHUNK):
    questions = bank.get("questions") or []
    order, groups = [], {}
    for q in questions:
        unit = q.get("unit") if isinstance(q, dict) else None
        key = "" if unit is None else str(unit)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(q)
    pieces, used = [], set()
    for key in order:
        items = groups[key]
        count = max(1, -(-len(items) // max_per_chunk))
        for n in range(count):
            part = items[n * max_per_chunk:(n + 1) * max_per_chunk]
            base = slug(key) + ("" if count == 1 else "-%d" % (n + 1))
            name, extra = base, 1
            while name in used:
                extra += 1
                name = "%s-%d" % (base, extra)
            used.add(name)
            pieces.append((PREFIX + name + ".enc", part))
    if len(pieces) < 2:
        return []
    head = {k: v for k, v in bank.items() if k != "questions"}
    out = []
    for i, (path, part) in enumerate(pieces):
        body = dict(head) if i == 0 else {}
        body["questions"] = part
        out.append((path, body))
    return out


def dump(bank):
    return json.dumps(bank, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def merge(parts):
    bank = dict(parts[0])
    bank["questions"] = [q for part in parts for q in part["questions"]]
    return bank
