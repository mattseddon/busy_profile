from __future__ import annotations

import random
import re

from busy_profile.text import TEMPLATES, random_sentence


def test_sentence_is_capitalised_and_terminated() -> None:
    sentence = random_sentence(random.Random(0))
    assert sentence[0].isupper()
    assert sentence.endswith(".")


def test_sentence_is_a_single_line() -> None:
    for seed in range(50):
        assert "\n" not in random_sentence(random.Random(seed))


def test_no_placeholders_are_left_unfilled() -> None:
    for seed in range(50):
        sentence = random_sentence(random.Random(seed))
        assert "{" not in sentence
        assert "}" not in sentence


def test_indefinite_article_agrees_with_the_following_word() -> None:
    for seed in range(400):
        sentence = random_sentence(random.Random(seed)).lower()
        articles: list[tuple[str, str]] = re.findall(r"\b(a|an) (\w)", sentence)
        for article, following in articles:
            starts_with_vowel = following in "aeiou"
            assert article == ("an" if starts_with_vowel else "a"), sentence


def test_same_seed_gives_the_same_sentence() -> None:
    assert random_sentence(random.Random(7)) == random_sentence(random.Random(7))


def test_generates_varied_sentences() -> None:
    rng = random.Random(0)
    sentences = {random_sentence(rng) for _ in range(200)}
    assert len(sentences) > 150


def test_every_template_formats_with_the_available_words() -> None:
    for template in TEMPLATES:
        rendered = template.format(
            adjective="one",
            other_adjective="two",
            noun="three",
            other_noun="four",
            verb="five",
            place="six",
        )
        assert "{" not in rendered
