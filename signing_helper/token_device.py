"""Enumerate DSC devices without logging in; never guess between devices."""


def connected_tokens(lib_location):
    import pkcs11

    # Empty reader slots can raise SlotIDInvalid after a USB token is removed.
    # Ask the driver only for slots where a token is currently present.
    return [slot.get_token() for slot in pkcs11.lib(lib_location).get_slots(token_present=True)]


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
