import secrets


def new_id() -> str:
    return secrets.token_hex(16)


def new_conversation_id() -> str:
    """Unguessable id: 128 bits of randomness. Fits the ConversationId pattern."""
    return secrets.token_urlsafe(16)
