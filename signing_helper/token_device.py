"""Enumerate DSC devices without logging in; never guess between devices."""


def connected_tokens(lib_location):
    import pkcs11

    return list(pkcs11.lib(lib_location).get_tokens())


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


def open_token_session(lib_location, pin, serial=None):
    token = choose_token(connected_tokens(lib_location), serial)
    return token.open(user_pin=pin)
