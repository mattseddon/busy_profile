"""Generation of the random sentences that each commit writes."""

from __future__ import annotations

import random
import re

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
    "sleepless",
    "velvet",
    "tarnished",
    "wandering",
    "obstinate",
    "faded",
    "clockwork",
    "hesitant",
    "amber",
    "forgotten",
    "threadbare",
    "salt-stained",
    "unhurried",
    "tidal",
    "ordinary",
    "borrowed",
    "moth-eaten",
    "cautious",
    "inconvenient",
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
    "meth",
    "locksmith",
    "tide",
    "typewriter",
    "beekeeper",
    "observatory",
    "violin",
    "pendulum",
    "postcard",
    "glacier",
    "librarian",
    "tramline",
    "windmill",
    "telescope",
    "umbrella",
    "compass",
    "almanac",
    "stationmaster",
    "seagull",
    "accordion",
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
    "borrows",
    "measures",
    "outwaits",
    "remembers",
    "repairs",
    "interrupts",
    "sketches",
    "translates",
    "hums to",
    "counts",
    "haunts",
    "polishes",
    "envies",
    "shadows",
    "whispers to",
    "auctions",
    "befriends",
    "postpones",
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
    "beneath the old bridge",
    "on the last day of summer",
    "in the attic",
    "past the tram depot",
    "along the seawall",
    "during the eclipse",
    "behind the observatory",
    "on a borrowed bicycle",
    "at low tide",
    "just before dawn",
    "down by the sidings",
    "in a borrowed coat",
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
    sentence = _A_BEFORE_VOWEL.sub("an ", sentence)
    return sentence[0].upper() + sentence[1:]
