"""The reference request and responses the API contract is written against, exactly as supplied.

The conversation id "conv_001" is the example's. The server issues its own ids, so an id it did
not issue is refused with CONVERSATION_NOT_FOUND. The example's id is kept here for the shape check.
"""
REFERENCE_REQUEST = {
    "conversation_id": "conv_001",
    "message": "Explain machine learning",
    "model": "rapid",
    "mode": "general",
    "attachments": [],
}

REFERENCE_SUCCESS_RESPONSE = {
    "conversation_id": "conv_001",
    "model": "rapid",
    "mode": "general",
    "content": "Machine learning is a branch of AI...",
    "status": "success",
    "error": None,
}

REFERENCE_ERROR_RESPONSE = {
    "conversation_id": "conv_001",
    "model": "rapid",
    "mode": "general",
    "content": None,
    "status": "error",
    "error": {"code": "VALIDATION_ERROR", "message": "The message field is required."},
}
