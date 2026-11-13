"""Error-message sanitation for caller-visible strings (defense in depth).

This module carries the minimal, fleet-standard code-point set + a ``sanitize_message``
helper used by the MCP error boundary. It is applied to EVERY caller-visible
error/message/diagnostics string so a hostile upstream body (or a caller-influenced
4xx/5xx response) can never smuggle control, zero-width, bidirectional, or NUL code
points into an error frame that reaches the model in either ``structured_content``
or the ``TextContent`` JSON mirror.

This is a backstop for **server-authored** strings only: it strips code points but
NOT injection prose. Attacker-influenceable upstream response bodies are additionally
kept out of caller-visible messages at their source (the API client severs them into
fixed, status-keyed messages -- see ``ucsc_link/api/client.py``).
"""

from __future__ import annotations

#: The ratified control/zero-width/bidi/NUL code points stripped from every
#: caller-visible message. Byte-identical to the fleet ``FORBIDDEN_CODEPOINTS``
#: set the module-fenced backends use in ``untrusted_content.py``.
FORBIDDEN_CODEPOINTS = frozenset(
    {
        *range(0x0000, 0x0009),
        *range(0x000B, 0x000D),
        *range(0x000E, 0x0020),
        *range(0x007F, 0x00A0),
        0x200B,
        0x200C,
        0x200D,
        0x2060,
        0xFEFF,
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
    }
)

#: Fleet-norm length cap for a caller-visible error message.
MAX_MESSAGE_CHARS = 280


def sanitize_message(text: str) -> str:
    """Strip the fence's forbidden control/zero-width/bidi/NUL code points + length-cap.

    Applied to EVERY caller-visible message/error/diagnostics string so a hostile
    upstream (or a caller-influenced 4xx/5xx body) can never smuggle control,
    zero-width, bidirectional, or NUL code points into an error frame. Caller-visible
    messages are server-authored guidance data; upstream response bodies are
    additionally kept out of them at the source (the API client).
    """
    clean = "".join(char for char in text if ord(char) not in FORBIDDEN_CODEPOINTS)
    return clean[:MAX_MESSAGE_CHARS]
