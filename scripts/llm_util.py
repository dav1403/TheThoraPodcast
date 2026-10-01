"""
llm_util.py
-----------
Shared helpers for reading Anthropic Messages API responses.

With extended/adaptive thinking enabled (the default on recent models),
`message.content[0]` can be a ThinkingBlock that has no `.text`, so a blind
`content[0].text` crashes on the first such response. Always go through
`first_text()`.
"""


def first_text(message) -> str:
    """Text of the first `text` content block, stripped; "" if there is none."""
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "text":
            return (getattr(block, "text", "") or "").strip()
    return ""
