"""The Mac has no lock-screen unlock, and the receiver is written to expect
one. `is_locked` answering None is how it says "no idea, carry on" -- the same
answer the Windows module gives when its external unlock credential provider
is absent.

A Mac at its own lock screen simply does not receive the PC's input; macOS
gives no application a way to type on it, exactly as Windows gives none for
Winlogon.
"""


def is_locked():
    return None


def ensure_unlocked():
    return False
