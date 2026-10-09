from types import SimpleNamespace

import pytest

from signing_helper.token_device import open_token_session
from signing_helper.token_profile import describe_token, validate_names, validate_certificate_profile


@pytest.mark.parametrize("name,profile", [("JISHNU NANDA", "eee"), ("VINOD (PALLIATRANS)", "pallia-billing"),
                                         ("Ramashankar Sharma", "pallia-accounts")])
def test_intended_identity_is_allowed(name, profile):
    validate_names([name], profile)
    cert = SimpleNamespace(subject=SimpleNamespace(native={"common_name": name}))
    validate_certificate_profile(cert, profile)


@pytest.mark.parametrize("name,profile", [("JISHNU NANDA", "pallia-billing"), ("JISHNU NANDA", "pallia-accounts"),
                                         ("VINOD", "eee"), ("RAMASHANKAR SHARMA", "eee")])
def test_signer_cannot_cross_flows(name, profile):
    with pytest.raises(ValueError, match="requires"):
        validate_names([name], profile)
    cert = SimpleNamespace(subject=SimpleNamespace(native={"common_name": name}))
    with pytest.raises(ValueError, match="Signing stopped"):
        validate_certificate_profile(cert, profile)


def test_jishnu_is_blocked_for_billing_before_pin_attempt(monkeypatch):
    token = SimpleNamespace(serial="jishnu")
    token.open = lambda **kwargs: pytest.fail("No PIN or signing session may be opened")
    monkeypatch.setattr("signing_helper.token_device.connected_tokens", lambda _: [token])
    monkeypatch.setattr("signing_helper.token_profile.public_signer_names", lambda _: ["JISHNU NANDA"])
    with pytest.raises(ValueError, match="requires VINOD"):
        open_token_session("driver", "pin", "jishnu", "pallia-billing")


def test_refresh_marks_jishnu_for_eee_only(monkeypatch):
    monkeypatch.setattr("signing_helper.token_profile.public_signer_names", lambda _: ["JISHNU NANDA"])
    assert describe_token(object(), "eee") == {"signer_name": "JISHNU NANDA", "profiles": ["eee"], "eligible": True}
    assert describe_token(object(), "pallia-billing")["eligible"] is False
    assert describe_token(object(), "pallia-accounts")["eligible"] is False


def test_empty_or_unknown_certificate_is_blocked():
    with pytest.raises(ValueError, match="requires"):
        validate_names([], "eee")
    with pytest.raises(ValueError, match="Unknown"):
        validate_names(["JISHNU NANDA"], "unknown")


def test_empty_driver_slots_are_not_probed(monkeypatch):
    import pkcs11
    calls = []

    class Library:
        def reinitialize(self):
            calls.append("refresh")

        def get_slots(self, **kwargs):
            calls.append(kwargs)
            return []

    monkeypatch.setattr(pkcs11, "lib", lambda _: Library())
    from signing_helper.token_device import connected_tokens
    assert connected_tokens("driver") == []
    assert calls == ["refresh", {"token_present": True}]


def test_refresh_observes_token_swap_in_the_same_process(monkeypatch):
    import pkcs11
    from signing_helper.token_device import connected_tokens

    class Library:
        current = "JISHNU NANDA"
        cached = current

        def reinitialize(self):
            self.cached = self.current

        def get_slots(self, **kwargs):
            return [SimpleNamespace(get_token=lambda: self.cached)]

    library = Library()
    monkeypatch.setattr(pkcs11, "lib", lambda _: library)
    assert connected_tokens("driver") == ["JISHNU NANDA"]
    library.current = "VINOD"
    assert connected_tokens("driver") == ["VINOD"]


def test_refresh_waits_for_active_signing_operation():
    import threading
    from signing_helper.token_device import TOKEN_LOCK, token_operation

    entered = threading.Event()

    @token_operation
    def refresh():
        entered.set()

    with TOKEN_LOCK:
        thread = threading.Thread(target=refresh)
        thread.start()
        assert not entered.wait(.05)
    thread.join(timeout=2)
    assert entered.is_set()
