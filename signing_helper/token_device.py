"""Enumerate DSC devices without logging in; never guess between devices."""
from functools import wraps
from threading import RLock

TOKEN_LOCK = RLock()


def token_operation(function):
    """Keep driver refresh from invalidating any in-progress signing session."""
    @wraps(function)
    def locked(*args, **kwargs):
        with TOKEN_LOCK:
            return function(*args, **kwargs)
    return locked


@token_operation
def connected_tokens(lib_location):
    import pkcs11

    # Empty reader slots can raise SlotIDInvalid after a USB token is removed.
    # Ask the driver only for slots where a token is currently present.
    library = pkcs11.lib(lib_location)
    # CryptoID caches the previous USB device in a long-running process.
    # All callers keep TOKEN_LOCK until their public/signing session closes.
    library.reinitialize()
    return [slot.get_token() for slot in library.get_slots(token_present=True)]


def token_serial(token):
    value = token.serial
    return value.decode().strip() if isinstance(value, bytes) else str(value).strip()


def choose_token(tokens, serial=None):
    matches = [token for token in tokens if token_serial(token) == serial] if serial else tokens
    if not matches:
        raise ValueError("Selected DSC token is not connected. Connect it and refresh the DSC list.")
    if len(matches) != 1:
        raise ValueError("Multiple DSC tokens are connected. Select the intended DSC before signing.")
    return matches[0]


def open_token_session(lib_location, pin, serial=None, profile=None):
    token = choose_token(connected_tokens(lib_location), serial)
    if profile:
        try:
            from .token_profile import validate_token_profile
        except ImportError:
            from token_profile import validate_token_profile
        validate_token_profile(token, profile)
    return token.open(user_pin=pin)
