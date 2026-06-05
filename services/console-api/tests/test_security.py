from console_api.security import (
    hash_password,
    hash_session_token,
    new_session_token,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    encoded = hash_password("secret")

    assert verify_password("secret", encoded)
    assert not verify_password("wrong", encoded)


def test_session_token_hash_is_stable() -> None:
    token = new_session_token()

    assert token
    assert hash_session_token(token) == hash_session_token(token)
    assert hash_session_token(token) != token
