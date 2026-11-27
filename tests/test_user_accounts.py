"""User accounts: an admin adds a user, and that user can sign in with the password the admin saved."""
import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import User, UserRole
from app.services.auth import get_password_hash

ADMIN = "accounts-admin@test.com"
NEW_USER = {"email": "new.user@test.com", "full_name": "New User", "password": "Welcome@123", "role": "user",
            "business": "eee_taxi", "permissions": ["eee_taxi"]}


def _remove_users():
    with SessionLocal() as db:
        db.query(User).filter(User.email.in_([ADMIN, NEW_USER["email"]])).delete(synchronize_session=False)
        db.commit()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        _remove_users()
        yield c
        _remove_users()


@pytest.fixture()
def admin(client):
    with SessionLocal() as db:
        db.add(User(email=ADMIN, hashed_password=get_password_hash("Password123"), role=UserRole.ADMIN, is_active=True))
        db.commit()
    token = client.post("/api/auth/login", json={"email": ADMIN, "password": "Password123"}).json()["access_token"]
    return {"Authorization": "Bearer " + token}


def _login(client, password, email=NEW_USER["email"]):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_a_user_the_admin_adds_can_sign_in(client, admin):
    created = client.post("/api/auth/register", json=NEW_USER, headers=admin)

    assert created.status_code == 201, created.text
    assert _login(client, "Welcome@123").status_code == 200
    assert _login(client, "Welcome@123", email="  New.User@Test.com ").status_code == 200   # typed with capitals
    assert _login(client, "welcome@123").status_code == 401                                  # the password is exact


def test_editing_a_user_keeps_the_password_unless_a_new_one_is_given(client, admin):
    user_id = client.post("/api/auth/register", json=NEW_USER, headers=admin).json()["id"]
    edit = {"full_name": "Renamed", "permissions": ["eee_taxi"], "business": "eee_taxi"}

    kept = client.patch(f"/api/auth/users/{user_id}", json=edit, headers=admin)
    assert kept.status_code == 200, kept.text
    assert _login(client, "Welcome@123").status_code == 200

    changed = client.patch(f"/api/auth/users/{user_id}", json={**edit, "password": "Changed@456"}, headers=admin)
    assert changed.status_code == 200, changed.text
    assert _login(client, "Changed@456").status_code == 200
    assert _login(client, "Welcome@123").status_code == 401


def test_a_password_of_numbers_only_works(client, admin):
    created = client.post("/api/auth/register", json={**NEW_USER, "password": "9000000001"}, headers=admin)

    assert created.status_code == 201, created.text
    assert _login(client, "9000000001").status_code == 200


@pytest.mark.parametrize("password", ["Welcome@123 ", " Welcome@123", "Welcome@123\t", "   ", "90000 00001"])
def test_a_password_with_a_space_in_it_is_refused(client, admin, password):
    """A mobile number typed as '90000 00001', or a password copied with a space nobody sees, locks the user out:
    behind the dots nobody can tell whether the space was typed again at sign-in."""
    refused = client.post("/api/auth/register", json={**NEW_USER, "password": password}, headers=admin)

    assert refused.status_code == 400, refused.text
    assert "space" in refused.json()["detail"]

    user_id = client.post("/api/auth/register", json=NEW_USER, headers=admin).json()["id"]
    refused = client.patch(f"/api/auth/users/{user_id}", json={"password": password}, headers=admin)

    assert refused.status_code == 400, refused.text
    assert _login(client, "Welcome@123").status_code == 200   # the old password still works
