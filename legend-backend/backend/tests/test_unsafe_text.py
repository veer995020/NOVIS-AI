"""The one-pass unsafe-character check must agree with the per-character definition it replaced.

The reference below is the original definition, kept here so the optimisation cannot change which
text is refused. The comparison covers every code point in the Basic Multilingual Plane, and a
sample of supplementary characters.
"""
import random
import unicodedata

from backend.schemas.common import contains_unsafe_character, is_unsafe_character

_ALLOWED = {"\n", "\t", "\r"}
_BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))


def reference_is_unsafe(char: str) -> bool:
    if char in _ALLOWED:
        return False
    if ord(char) in _BIDI:
        return True
    return unicodedata.category(char) in ("Cc", "Cs")


def test_single_characters_agree_across_the_basic_plane() -> None:
    for code in range(0x10000):
        char = chr(code)
        assert is_unsafe_character(char) == reference_is_unsafe(char), hex(code)


def test_supplementary_characters_agree_on_a_sample() -> None:
    rng = random.Random(7)
    for _ in range(5000):
        char = chr(rng.randrange(0x10000, 0x110000))
        assert is_unsafe_character(char) == reference_is_unsafe(char), hex(ord(char))


def test_text_is_unsafe_exactly_when_one_of_its_characters_is() -> None:
    rng = random.Random(11)
    alphabet = ["a", " ", "\n", "\t", "\r", "\x00", "\x07", "\x0b", "\x7f", "\x9f", "\ud800", "\udfff",
                "\u202e", "\u2066", "é", "😀", "\u200b"]
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 12)))
        expected = any(reference_is_unsafe(char) for char in text)
        assert contains_unsafe_character(text) == expected, repr(text)


def test_a_paired_emoji_is_safe_and_a_lone_surrogate_is_not() -> None:
    assert not contains_unsafe_character("ok 😀 fine")
    assert contains_unsafe_character("bad \ud83d alone")
