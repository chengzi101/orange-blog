"""Small shared input-safe constant-time comparison for CSRF/auth text."""
import hmac


def constant_time_text_equal(expected: str, received: str) -> bool:
    if not isinstance(expected, str) or not isinstance(received, str):
        return False
    # compare_digest(str, str) raises TypeError for non-ASCII user input.
    return hmac.compare_digest(expected.encode('utf-8'), received.encode('utf-8'))
