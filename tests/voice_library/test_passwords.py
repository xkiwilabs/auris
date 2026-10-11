from __future__ import annotations

import pytest

from auris.voice_library import passwords


def test_hash_then_verify_round_trip():
    stored = passwords.hash_password("correct horse battery")
    assert passwords.verify_password("correct horse battery", stored) is True
    assert passwords.verify_password("Correct horse battery", stored) is False


def test_hash_is_salted_so_two_hashes_of_one_password_differ():
    a = passwords.hash_password("correct horse battery")
    b = passwords.hash_password("correct horse battery")
    assert a != b
    assert passwords.verify_password("correct horse battery", a)
    assert passwords.verify_password("correct horse battery", b)


def test_stored_format_is_self_describing():
    stored = passwords.hash_password("correct horse battery")
    algo, n, r, p, salt, digest = stored.split("$")
    assert algo == "scrypt"
    assert (int(n), int(r), int(p)) == (32768, 8, 1)
    assert salt and digest


def test_a_hash_made_with_old_parameters_still_verifies():
    """Parameters are re-read from the stored string, so raising them is not a flag day."""
    weak = passwords.hash_password("correct horse battery", n=1024)
    assert weak.split("$")[1] == "1024"
    assert passwords.verify_password("correct horse battery", weak) is True


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "not-a-hash",
        "scrypt$32768$8$1$onlyfivefields",
        "bcrypt$32768$8$1$c2FsdA==$ZGlnZXN0",  # unknown algorithm
        "scrypt$notanint$8$1$c2FsdA==$ZGlnZXN0",
        "scrypt$32768$8$1$!!!notbase64!!!$ZGlnZXN0",
        "scrypt$-5$8$1$c2FsdA==$ZGlnZXN0",  # negative n → TypeError from hashlib, not ValueError
    ],
)
def test_a_corrupt_stored_hash_returns_false_and_never_raises(stored):
    """A row read from the DB is not attacker-controlled, but a raise here would turn one bad
    row into a 500 on every sign-in attempt for that account."""
    assert passwords.verify_password("anything", stored) is False


def test_a_password_under_the_minimum_is_refused():
    with pytest.raises(ValueError, match="at least 12"):
        passwords.hash_password("short")


def test_validate_password_returns_the_password_unchanged():
    assert passwords.validate_password("correct horse battery") == "correct horse battery"


def test_generated_passwords_are_long_unique_and_valid():
    generated = {passwords.generate_password() for _ in range(20)}
    assert len(generated) == 20
    for pw in generated:
        assert len(pw) >= passwords.MIN_LENGTH
        assert passwords.validate_password(pw) == pw
