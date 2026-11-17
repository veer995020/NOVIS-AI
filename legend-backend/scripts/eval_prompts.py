"""Run the evaluation cases against a running Novis server and apply the quality checks.

Usage:  python scripts/eval_prompts.py --base-url http://127.0.0.1:8000 [--model rapid]

Each case is one or more turns sent in order to /api/chat, reusing the conversation id. A case fails
on any hard reply defect (see backend/prompts/quality.py HARD_CODES) or any unmet expectation. The
script exits with status 1 when any case fails. It uses only the standard library.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.prompts.quality import HARD_CODES, check_reply  # noqa: E402


def post_chat(base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    if not base_url.startswith(("http://", "https://")):
        raise SystemExit("--base-url must start with http:// or https://")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read() or b"{}")


def run_case(case: dict[str, Any], base_url: str, model: str) -> tuple[list[str], str]:
    """Returns (failures, last reply). An empty failure list means the case passed."""
    failures: list[str] = []
    conversation_id: str | None = None
    reply = ""
    for turn in case["turns"]:
        body: dict[str, Any] = {"message": turn["message"], "model": model,
                                "mode": case.get("mode", "general"), "attachments": turn.get("attachments", [])}
        if conversation_id:
            body["conversation_id"] = conversation_id
        data = post_chat(base_url, body)
        conversation_id = data.get("conversation_id") or conversation_id
        if data.get("status") != "success":
            return [f"request failed: {data.get('error')}"], ""
        reply = data.get("content") or ""

    lowered = reply.lower()
    depth = case.get("depth", "standard")
    for issue in check_reply(reply, mode=case.get("mode", "general"), depth=depth):
        if issue.code in HARD_CODES:
            failures.append(f"defect {issue.code}: {issue.detail}")
    for needle in case.get("contains", []):
        if needle.lower() not in lowered:
            failures.append(f"missing '{needle}'")
    for needle in case.get("not_contains", []):
        if needle.lower() in lowered:
            failures.append(f"contains forbidden '{needle}'")
    if case.get("any_of") and not any(option.lower() in lowered for option in case["any_of"]):
        failures.append("none of the expected phrases appear")
    if "max_chars" in case and len(reply) > case["max_chars"]:
        failures.append(f"{len(reply)} characters, over the limit of {case['max_chars']}")
    if "min_chars" in case and len(reply) < case["min_chars"]:
        failures.append(f"{len(reply)} characters, under the minimum of {case['min_chars']}")
    if case.get("requires_bold") and "**" not in reply:
        failures.append("no bold final answer")
    return failures, reply


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="rapid")
    parser.add_argument("--cases", default=str(Path(__file__).with_name("eval_cases.json")))
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    failed = 0
    for case in cases:
        failures, _ = run_case(case, args.base_url, args.model)
        status = "PASS" if not failures else "FAIL"
        failed += bool(failures)
        print(f"{status}  {case['name']}")
        for failure in failures:
            print(f"      - {failure}")
    print(f"\n{len(cases) - failed} of {len(cases)} cases passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
