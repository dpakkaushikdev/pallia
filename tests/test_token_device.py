import pytest

from signing_helper.token_device import choose_token, open_token_session


class Token:
    def __init__(self, serial):
        self.serial = serial


def test_selects_exact_device_among_three_distinct_dscs():
    tokens = [Token(b"EEE "), Token("billing"), Token("accounts")]
    assert choose_token(tokens, "accounts") is tokens[2]
    assert choose_token(tokens, "EEE") is tokens[0]
    with pytest.raises(ValueError, match="Multiple DSC"):
        choose_token(tokens)


def test_disconnected_selected_device_never_falls_back_to_another():
    with pytest.raises(ValueError, match="not connected"):
        choose_token([Token("billing")], "accounts")


def test_wrong_device_is_rejected_before_pin_is_used(monkeypatch):
    token = Token("billing")
    token.open = lambda **kwargs: pytest.fail("Must not attempt PIN on another token")
    monkeypatch.setattr("signing_helper.token_device.connected_tokens", lambda _: [token])
    with pytest.raises(ValueError, match="not connected"):
        open_token_session("driver", "pin", "accounts")
