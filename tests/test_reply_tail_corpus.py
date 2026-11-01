"""Pin the stop-path counts that tests/reply_tail_corpus.py measures (#264)."""

import code_forge.llm_invoke as invoke
from code_forge.reviewer_json import REVIEW_REPLY_KEYS
from tests import reply_tail_corpus as corpus


def _lost(kind, keys):
    return sorted(
        (place, text)
        for k, place, text in corpus.cases()
        if k == kind and corpus._reply_object(corpus.stop_reply(invoke, text, keys)) is not None
    )


def test_corpus_size():
    kinds = [kind for kind, _, _ in corpus.cases()]
    assert (kinds.count("cut_tails"), kinds.count("whitespace_tails")) == (171, 9)
    assert kinds.count("valid_tails") == 104


def test_with_review_keys_only_a_leading_cut_object_loses_the_p1():
    counts = corpus.measure(invoke, REVIEW_REPLY_KEYS)
    assert counts["cut_tails"]["by_place"] == {"leading": 8, "list": 0, "prose": 0}
    assert counts["whitespace_tails"]["by_place"] == {"leading": 3, "list": 0, "prose": 0}
    tails = sorted(text[len(corpus.CUT) :] for _, text in _lost("cut_tails", REVIEW_REPLY_KEYS))
    assert tails == sorted(
        [" done", " (see a.py)", "\n- note", " 'a'", " for k in d:", " or y", " in d", "!"]
    )


def test_without_keys_prose_led_cut_objects_are_the_rest_of_the_limit():
    counts = corpus.measure(invoke, None)
    assert counts["cut_tails"]["by_place"] == {"leading": 8, "list": 0, "prose": 13}
    assert counts["whitespace_tails"]["by_place"] == {"leading": 3, "list": 0, "prose": 3}


def test_valid_replies_refused():
    assert corpus.measure(invoke, None)["valid_tails"]["by_place"] == {
        "leading": 4,
        "list": 4,
        "prose": 4,
    }
    # With keys a prose-led reply goes to the correction retry.
    with_keys = corpus.measure(invoke, REVIEW_REPLY_KEYS)["valid_tails"]["by_place"]
    assert with_keys == {"leading": 4, "list": 4, "prose": 40}
