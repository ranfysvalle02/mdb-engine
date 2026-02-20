"""Tests for mdb_engine.auth.jwt.validate_jwt_token_format."""

from mdb_engine.auth.jwt import validate_jwt_token_format


class TestValidateJwtTokenFormat:
    """Tests for the JWT format validator."""

    def test_valid_three_part_token(self):
        assert validate_jwt_token_format("header.payload.signature") is True

    def test_valid_realistic_token(self):
        token = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        assert validate_jwt_token_format(token) is True

    def test_none_input(self):
        assert validate_jwt_token_format(None) is False

    def test_empty_string(self):
        assert validate_jwt_token_format("") is False

    def test_non_string_input(self):
        assert validate_jwt_token_format(12345) is False
        assert validate_jwt_token_format(True) is False

    def test_too_short(self):
        assert validate_jwt_token_format("a.b.c") is False

    def test_too_long(self):
        assert validate_jwt_token_format("a" * 8193) is False

    def test_one_part(self):
        assert validate_jwt_token_format("justonepart_noperiods") is False

    def test_two_parts(self):
        assert validate_jwt_token_format("header.payload") is False

    def test_four_parts(self):
        assert validate_jwt_token_format("a.b.c.d") is False

    def test_empty_first_part(self):
        assert validate_jwt_token_format(".payload.signature") is False

    def test_empty_middle_part(self):
        assert validate_jwt_token_format("header..signature") is False

    def test_empty_last_part(self):
        assert validate_jwt_token_format("header.payload.") is False

    def test_minimum_valid_length(self):
        assert validate_jwt_token_format("abcd.efgh.ij") is True

    def test_at_max_length(self):
        part = "x" * 2000
        token = f"{part}.{part}.{part}"
        assert validate_jwt_token_format(token) is True

    def test_over_max_length(self):
        part = "x" * 3000
        token = f"{part}.{part}.{part}"
        assert validate_jwt_token_format(token) is False
