import json


def format_sse(event: dict) -> str:
    """Encode one event as a Server-Sent Events frame."""
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
