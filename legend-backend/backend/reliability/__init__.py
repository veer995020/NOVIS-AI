"""Reliability and security checks for inputs, replies, errors, and tool calls.

Every check is a pure function with a typed result. Nothing here fetches, executes, or verifies
anything outside the conversation, and no result claims that a fact is verified. A "traceable" claim
is one whose specific details appear in the user's own messages or attachments. That is all the
server can establish.
"""
