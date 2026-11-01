from ffl import passwords


def test_hash_and_verify():
    h = passwords.hash_password("x")
    assert h.startswith("scrypt$")
    assert h != passwords.hash_password("x")
    assert passwords.verify("x", h)
    assert not passwords.verify("y", h)


def test_verify_garbage_is_false():
    for stored in ("garbage", "scrypt$!!$!!", "scrypt$a$b$c", ""):
        assert not passwords.verify("x", stored)


def test_check_username():
    assert passwords.check_username(" Jon_1 ") == "Jon_1"
    assert passwords.check_username("a.b-c") == "a.b-c"
    for bad in ("a b", "x", "a" * 33, "jo\nn"):
        assert passwords.check_username(bad) is None
    assert passwords.check_username("a" * 32) == "a" * 32


def test_min_password():
    assert passwords.MIN_PASSWORD == 8
