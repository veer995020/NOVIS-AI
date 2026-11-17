"""The policy-fragment check is optimised. Its output must not change.

The reference below is the original algorithm, kept verbatim. The test compares it with the current
implementation on replies that repeat policy text with varied whitespace, near misses, and noise.
"""
import random
import re

from backend.prompts.system import build_system_prompt
from backend.reliability import output_guard
from backend.reliability.secret_guard import WITHHELD

WINDOW = output_guard._POLICY_WINDOW


def _reference(reply: str, policy: str) -> tuple[str, bool]:
    found = False
    compact_policy = " ".join(policy.split()).lower()
    windows = {compact_policy[i : i + WINDOW] for i in range(len(compact_policy) - WINDOW + 1)}
    lowered_reply = reply.lower()
    for fragment in sorted(windows):
        if fragment in lowered_reply:
            pattern = re.compile(r"\s+".join(re.escape(word) for word in fragment.split()), re.IGNORECASE)
            reply, count = pattern.subn(WITHHELD, reply)
            found = found or count > 0
            lowered_reply = reply.lower()
    return reply, found


def _cases(seed: int, count: int):
    policies = [
        build_system_prompt(mode, depth=depth)
        for mode in ("general", "research", "coding", "writing")
        for depth in ("brief", "standard")
    ]
    policies += ["short policy text", "Ab" * 30, ""]
    rng = random.Random(seed)
    for _ in range(count):
        policy = rng.choice(policies)
        words = policy.split()
        parts: list[str] = []
        for _ in range(rng.randint(0, 12)):
            roll = rng.random()
            if roll < 0.35 and len(words) > 45:
                start = rng.randrange(len(words) - 45)
                segment = [w.upper() if rng.random() < 0.2 else w for w in words[start : start + rng.randint(3, 40)]]
                parts.append(rng.choice([" ", "  ", "\n", "\t "]).join(segment))
            elif roll < 0.5 and len(words) > 45:
                start = rng.randrange(len(words) - 45)
                parts.append(" ".join(words[start : start + rng.randint(1, 35)]))
            else:
                parts.append("".join(rng.choice("abc xyz .,\n") for _ in range(rng.randint(0, 80))))
        yield policy, rng.choice([" ", "\n"]).join(parts)


def test_the_optimised_check_matches_the_original_on_varied_replies() -> None:
    checked_positive = 0
    for policy, reply in _cases(seed=7, count=400):
        expected = _reference(reply, policy)
        assert output_guard.withhold_policy_fragments(reply, policy) == expected
        checked_positive += expected[1]
    assert checked_positive > 50  # the sample really exercises the withholding path


def test_a_plain_reply_is_returned_unchanged() -> None:
    policy = build_system_prompt("general", depth="standard")
    reply = "A short, ordinary answer about the weather tomorrow."
    assert output_guard.withhold_policy_fragments(reply, policy) == (reply, False)
