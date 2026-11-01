"""Measure what the stop path takes from a reply with text after it (#264).

The corpus (tests/fixtures/reply_tail_corpus.json) has three kinds of text:

- cut tails: the reply's `code_excerpts` came first, an excerpt quoted
  `"}], "findings": []}` unescaped, and the stream stopped after the quote
  and this tail. Any object returned is the quoted empty review: the P1
  that the rest of the reply held is lost.
- whitespace tails: the same, with only whitespace after the quote.
- valid tails: a whole reply with a P1, then trailing text. A reply that
  does not come back with its P1 is refused (on the API path that costs
  the correction call).

Each text is tried in three places: leading (it opens the text), list
(after '[') and prose (after a prose prefix). Valid replies also follow
four prose prefixes, with and without a trailing newline.

A text goes through `_loads_model_json` and, if that raises, through
`_fallback_reply`, as `_invoke_api` does on a `stop` reply. Run from the
repository root:

    PYTHONPATH=src:. python tests/reply_tail_corpus.py [REV ...]

Each REV (a commit such as 880b069) is measured from `git show` as well.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import types
from pathlib import Path

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "reply_tail_corpus.json").read_text(encoding="utf-8")
)
P1 = '{"file": "a.py", "line": 3, "severity": "P1", "description": "d"}'
REAL = '{"findings": [%s], "code_excerpts": []}' % P1
CUT = (
    '{"code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 1, "content": "x"}], '
    '"findings": []}'
)
PLACES = ("leading", "list", "prose")


def _placed(obj: str, tail: str, place: str, close_list: bool) -> str:
    if place == "leading":
        return obj + tail
    if place == "list":
        return "[" + obj + ("]" if close_list else "") + tail
    return "Review: " + obj + tail


def cases():
    """(kind, place, text) for every case in the corpus."""
    for kind in ("cut_tails", "whitespace_tails"):
        for tail in CORPUS[kind]:
            for place in PLACES:
                yield kind, place, _placed(CUT, tail, place, close_list=False)
    for tail in CORPUS["valid_tails"]:
        for place in PLACES:
            yield "valid_tails", place, _placed(REAL, tail, place, close_list=True)
    for prefix in CORPUS["prose_prefixes"]:
        for end in ("", "\n"):
            yield "valid_tails", "prose", prefix + REAL + end


def stop_reply(module, text: str, keys):
    """What the stop path of `module` takes from `text`, or None."""
    try:
        return module._loads_model_json(text, keys)
    except json.JSONDecodeError as exc:
        if len(inspect.signature(module._fallback_reply).parameters) == 4:
            return module._fallback_reply(text, exc, None, keys)
        return module._fallback_reply(text, exc, None)
    except RecursionError:
        return None


def _reply_object(parsed):
    if isinstance(parsed, list) and len(parsed) == 1:
        parsed = parsed[0]
    return parsed if isinstance(parsed, dict) else None


def measure(module, keys) -> dict:
    """Counts per kind: lost P1s for cut texts, refused replies for valid ones."""
    counts: dict = {}
    for kind, place, text in cases():
        row = counts.setdefault(kind, {"total": 0, "hit": 0, "by_place": dict.fromkeys(PLACES, 0)})
        row["total"] += 1
        try:
            obj = _reply_object(stop_reply(module, text, keys))
        except json.JSONDecodeError:
            obj = None
        if kind == "valid_tails":
            hit = obj is None or "P1" not in [f.get("severity") for f in obj.get("findings", [])]
        else:
            hit = obj is not None
            if hit:
                assert obj.get("findings") == [], (text, obj)
        if hit:
            row["hit"] += 1
            row["by_place"][place] += 1
    return counts


def _module_at(rev: str):
    source = subprocess.run(
        ["git", "show", "%s:src/code_forge/llm_invoke.py" % rev],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    name = "code_forge._llm_invoke_at_%s" % rev
    module = types.ModuleType(name)
    module.__package__ = "code_forge"
    sys.modules[name] = module
    exec(compile(source, "%s:llm_invoke.py" % rev, "exec"), module.__dict__)  # noqa: S102
    return module


def _row(label: str, counts: dict) -> str:
    def cell(kind):
        row = counts[kind]
        places = ", ".join("%s %d" % (p, row["by_place"][p]) for p in PLACES)
        return "%d of %d (%s)" % (row["hit"], row["total"], places)

    return "| %s | %s | %s | %s |" % (
        label,
        cell("cut_tails"),
        cell("whitespace_tails"),
        cell("valid_tails"),
    )


def main(revs: list[str]) -> None:
    import code_forge.llm_invoke as current
    from code_forge.reviewer_json import REVIEW_REPLY_KEYS

    modules = [("working tree", current)] + [(rev, _module_at(rev)) for rev in revs]
    print("| Rule, keys | Cut: P1 lost | Whitespace: P1 lost | Valid: refused |")
    print("|---|---|---|---|")
    for label, module in modules:
        for keys_label, keys in (("no keys", None), ("review keys", REVIEW_REPLY_KEYS)):
            print(_row("%s, %s" % (label, keys_label), measure(module, keys)))


if __name__ == "__main__":
    main(sys.argv[1:])
