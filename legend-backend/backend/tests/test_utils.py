import json

from backend.utils.ids import new_id
from backend.utils.sse import format_sse


def test_ids_are_unique_hex() -> None:
    first, second = new_id(), new_id()
    assert first != second
    assert len(first) == 32


def test_format_sse_frames_event() -> None:
    frame = format_sse({"type": "delta", "text": "hi"})
    assert frame.startswith("event: delta\ndata: ")
    assert frame.endswith("\n\n")
    assert json.loads(frame.split("data: ", 1)[1]) == {"type": "delta", "text": "hi"}
