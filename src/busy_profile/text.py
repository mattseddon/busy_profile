"""Generation of the random sentences that each commit writes."""

from __future__ import annotations

import random
import re

RANDOM_TEXT_FILE = "random_text"

_A_BEFORE_VOWEL = re.compile(r"\ba (?=[aeiou])")

ADJECTIVES = (
    "quiet",
    "restless",
    "crooked",
    "gilded",
    "hollow",
    "patient",
    "luminous",
    "stubborn",
    "weathered",
    "idle",
    "brittle",
    "eager",
)

NOUNS = (
    "lighthouse",
    "archivist",
    "ferry",
    "orchard",
    "compiler",
    "kettle",
    "sparrow",
    "cartographer",
    "greenhouse",
    "metronome",
    "harbour",
    "lantern",
)

VERBS = (
    "forgets",
    "rearranges",
    "outlasts",
    "questions",
    "inherits",
    "misplaces",
    "catalogues",
    "abandons",
    "rehearses",
    "shelters",
    "unsettles",
    "mimics",
)

PLACES = (
    "by the harbour",
    "in the long grass",
    "before the rain",
    "under a paper moon",
    "at the edge of the map",
    "between two winters",
    "after the last train",
    "somewhere near the river",
)

TEMPLATES = (
    "the {adjective} {noun} {verb} the {other_adjective} {other_noun} {place}.",
    "a {adjective} {noun} {verb} everything {place}.",
    "{place}, the {adjective} {noun} {verb} the {other_noun}.",
    "every {noun} {verb} a {other_adjective} {other_noun}.",
    "the {noun} {verb} the {other_noun}, {place}.",
)


def random_sentence(rng: random.Random) -> str:
    """Build one capitalised sentence from ``rng``."""
    sentence = rng.choice(TEMPLATES).format(
        adjective=rng.choice(ADJECTIVES),
        other_adjective=rng.choice(ADJECTIVES),
        noun=rng.choice(NOUNS),
        other_noun=rng.choice(NOUNS),
        verb=rng.choice(VERBS),
        place=rng.choice(PLACES),
    )
    # The templates hardcode "a", which reads wrong before a vowel.
    sentence = _A_BEFORE_VOWEL.sub("an ", sentence)
    return sentence[0].upper() + sentence[1:]
