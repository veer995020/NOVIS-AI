import secrets
import uuid


def new_id() -> str:
    return uuid.uuid4().hex


def new_conversation_id() -> str:
    """Unguessable id: 128 bits of randomness. Fits the ConversationId pattern."""
    return secrets.token_urlsafe(16)
