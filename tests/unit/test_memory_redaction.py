"""
Unit tests for the RedactionService.

Tests cover:
- Built-in pattern matching (SSN, credit card, phone, email, etc.)
- Custom pattern support
- Allow list functionality
- Configuration options
- Edge cases and error handling
"""

from mdb_engine.redaction import RegexpRedactionService, get_redaction_service

# Backward compatibility aliases
RedactionService = RegexpRedactionService
create_redaction_service = get_redaction_service


class TestRedactionServiceBasic:
    """Test basic RedactionService functionality."""

    def test_init_default_config(self):
        """Test initialization with default configuration."""
        service = RedactionService()

        assert service.enabled is True
        assert service.replacement == "[REDACTED]"
        assert len(service._compiled_patterns) > 0  # noqa: SLF001

    def test_init_disabled(self):
        """Test initialization with service disabled."""
        service = RedactionService(config={"enabled": False})

        assert service.enabled is False

    def test_init_custom_replacement(self):
        """Test initialization with custom replacement text."""
        service = RedactionService(config={"replacement": "***"})

        assert service.replacement == "***"

    def test_factory_function(self):
        """Test factory function creates service correctly."""
        service = create_redaction_service(config={"enabled": True})

        assert isinstance(service, RedactionService)
        assert service.enabled is True


class TestSSNRedaction:
    """Test Social Security Number redaction."""

    def test_redact_ssn_standard_format(self):
        """Test redacting SSN in standard format."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"ssn": True},
            }
        )

        text = "My SSN is 123-45-6789"
        result = service.redact(text)

        assert "123-45-6789" not in result
        assert "[REDACTED]" in result

    def test_redact_multiple_ssns(self):
        """Test redacting multiple SSNs in text."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"ssn": True},
            }
        )

        text = "SSN 123-45-6789 and 987-65-4321"
        result = service.redact(text)

        assert "123-45-6789" not in result
        assert "987-65-4321" not in result
        assert result.count("[REDACTED]") == 2

    def test_ssn_disabled(self):
        """Test SSN redaction when disabled."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"ssn": False},
            }
        )

        text = "My SSN is 123-45-6789"
        result = service.redact(text)

        assert "123-45-6789" in result


class TestCreditCardRedaction:
    """Test credit card number redaction."""

    def test_redact_credit_card_spaces(self):
        """Test redacting credit card with spaces."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"credit_card": True},
            }
        )

        text = "Card: 4111 1111 1111 1111"
        result = service.redact(text)

        assert "4111 1111 1111 1111" not in result
        assert "[REDACTED]" in result

    def test_redact_credit_card_dashes(self):
        """Test redacting credit card with dashes."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"credit_card": True},
            }
        )

        text = "Card: 4111-1111-1111-1111"
        result = service.redact(text)

        assert "4111-1111-1111-1111" not in result

    def test_redact_credit_card_continuous(self):
        """Test redacting credit card without separators."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"credit_card": True},
            }
        )

        text = "Card: 4111111111111111"
        result = service.redact(text)

        assert "4111111111111111" not in result


class TestPhoneRedaction:
    """Test phone number redaction."""

    def test_redact_phone_us_format(self):
        """Test redacting US phone number."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"phone": True},
            }
        )

        text = "Call me at (555) 123-4567"
        result = service.redact(text)

        assert "(555) 123-4567" not in result

    def test_redact_phone_with_country_code(self):
        """Test redacting phone with country code."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"phone": True},
            }
        )

        text = "Number: +1-555-123-4567"
        result = service.redact(text)

        assert "+1-555-123-4567" not in result

    def test_phone_disabled_by_default(self):
        """Test phone redaction is disabled by default."""
        service = RedactionService(config={"enabled": True})

        text = "Call me at 555-123-4567"
        result = service.redact(text)

        # Phone is disabled by default, so it should remain
        assert "555-123-4567" in result


class TestEmailRedaction:
    """Test email address redaction."""

    def test_redact_email(self):
        """Test redacting email address."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"email": True},
            }
        )

        text = "Email me at user@example.com"
        result = service.redact(text)

        assert "user@example.com" not in result

    def test_email_disabled_by_default(self):
        """Test email redaction is disabled by default."""
        service = RedactionService(config={"enabled": True})

        text = "Email: user@example.com"
        result = service.redact(text)

        # Email is disabled by default
        assert "user@example.com" in result


class TestPasswordAndAPIKeyRedaction:
    """Test password and API key redaction."""

    def test_redact_password_assignment(self):
        """Test redacting password assignments."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"password": True},
            }
        )

        text = "password=secret123"
        result = service.redact(text)

        assert "secret123" not in result

    def test_redact_password_with_colon(self):
        """Test redacting password with colon format."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"password": True},
            }
        )

        text = "password: mysecretpass"
        result = service.redact(text)

        assert "mysecretpass" not in result

    def test_redact_api_key(self):
        """Test redacting API key."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"api_key": True},
            }
        )

        text = "api_key=abc123xyz"
        result = service.redact(text)

        assert "abc123xyz" not in result

    def test_redact_bearer_token(self):
        """Test redacting Bearer token."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"bearer_token": True},
            }
        )

        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc123"
        result = service.redact(text)

        assert "eyJhbGciOiJIUzI1NiJ9" not in result


class TestCustomPatterns:
    """Test custom pattern support."""

    def test_custom_pattern_simple(self):
        """Test simple custom pattern."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"custom": [r"\bsecret_\w+"]},
            }
        )

        text = "The secret_code is here"
        result = service.redact(text)

        assert "secret_code" not in result

    def test_custom_pattern_complex(self):
        """Test complex custom pattern."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"custom": [r"internal_id:\s*\d+"]},
            }
        )

        text = "Check internal_id: 12345"
        result = service.redact(text)

        assert "internal_id: 12345" not in result

    def test_multiple_custom_patterns(self):
        """Test multiple custom patterns."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {
                    "custom": [
                        r"project_\w+",
                        r"employee_id:\s*\d+",
                    ]
                },
            }
        )

        text = "Working on project_alpha, employee_id: 999"
        result = service.redact(text)

        assert "project_alpha" not in result
        assert "employee_id: 999" not in result

    def test_invalid_custom_pattern_ignored(self):
        """Test that invalid custom patterns are ignored."""
        # Invalid regex pattern (unclosed bracket)
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"custom": [r"[invalid"]},
            }
        )

        # Should not crash
        text = "Some text"
        result = service.redact(text)
        assert result == text


class TestAllowList:
    """Test allow list functionality."""

    def test_allow_list_email(self):
        """Test allow list prevents email redaction."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"email": True},
                "allow_list": ["support@company.com"],
            }
        )

        text = "Contact support@company.com or user@other.com"
        result = service.redact(text)

        assert "support@company.com" in result
        assert "user@other.com" not in result

    def test_add_to_allow_list(self):
        """Test adding to allow list at runtime."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"email": True},
            }
        )

        service.add_to_allow_list("allowed@example.com")

        text = "Contact allowed@example.com"
        result = service.redact(text)

        assert "allowed@example.com" in result

    def test_remove_from_allow_list(self):
        """Test removing from allow list."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"email": True},
                "allow_list": ["test@example.com"],
            }
        )

        service.remove_from_allow_list("test@example.com")

        text = "Contact test@example.com"
        result = service.redact(text)

        assert "test@example.com" not in result


class TestDictRedaction:
    """Test dictionary redaction."""

    def test_redact_dict_string_values(self):
        """Test redacting string values in dict."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"ssn": True},
            }
        )

        data = {
            "name": "John",
            "ssn": "123-45-6789",
        }

        result = service.redact_dict(data)

        assert result["name"] == "John"
        assert "123-45-6789" not in result["ssn"]

    def test_redact_dict_nested(self):
        """Test redacting nested dict values."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"ssn": True},
            }
        )

        data = {
            "user": {
                "ssn": "123-45-6789",
            },
        }

        result = service.redact_dict(data)

        assert "123-45-6789" not in result["user"]["ssn"]

    def test_redact_dict_specific_fields(self):
        """Test redacting only specific fields in dict."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"ssn": True},
            }
        )

        data = {
            "public_id": "123-45-6789",  # Looks like SSN but in public_id field
            "private_ssn": "987-65-4321",
        }

        result = service.redact_dict(data, fields=["private_ssn"])

        # Only private_ssn should be redacted
        assert result["public_id"] == "123-45-6789"
        assert "987-65-4321" not in result["private_ssn"]


class TestRuntimePatternAddition:
    """Test runtime pattern addition."""

    def test_add_pattern_at_runtime(self):
        """Test adding a pattern at runtime."""
        service = RedactionService(config={"enabled": True})

        success = service.add_pattern("test_pattern", r"secret_\d+")

        assert success is True

        text = "The secret_123 is here"
        result = service.redact(text)

        assert "secret_123" not in result

    def test_add_invalid_pattern(self):
        """Test adding invalid pattern returns False."""
        service = RedactionService(config={"enabled": True})

        success = service.add_pattern("bad_pattern", r"[invalid")

        assert success is False


class TestTestRedaction:
    """Test the test_redaction method."""

    def test_test_redaction_with_matches(self):
        """Test test_redaction returns match info."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"ssn": True},
            }
        )

        result = service.test_redaction("SSN: 123-45-6789")

        assert result["would_redact"] is True
        assert "123-45-6789" in result["matches"]
        assert "ssn" in result["pattern_matches"]

    def test_test_redaction_no_matches(self):
        """Test test_redaction with no matches."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"ssn": True},
            }
        )

        result = service.test_redaction("Hello world")

        assert result["would_redact"] is False
        assert len(result["matches"]) == 0


class TestStats:
    """Test statistics retrieval."""

    def test_get_stats(self):
        """Test getting service statistics."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"ssn": True, "credit_card": True},
            }
        )

        stats = service.get_stats()

        assert stats["enabled"] is True
        assert stats["replacement"] == "[REDACTED]"
        assert stats["active_patterns"] >= 2
        assert "ssn" in stats["pattern_names"]


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_text(self):
        """Test redacting empty text."""
        service = RedactionService(config={"enabled": True})

        result = service.redact("")

        assert result == ""

    def test_none_text(self):
        """Test redacting None (should return as-is)."""
        service = RedactionService(config={"enabled": True})

        result = service.redact(None)

        assert result is None

    def test_disabled_service_passthrough(self):
        """Test disabled service passes text through."""
        service = RedactionService(config={"enabled": False})

        text = "SSN: 123-45-6789, password=secret"
        result = service.redact(text)

        assert result == text

    def test_no_patterns_enabled(self):
        """Test with all patterns disabled."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {
                    "ssn": False,
                    "credit_card": False,
                    "phone": False,
                    "email": False,
                    "api_key": False,
                    "password": False,
                    "bearer_token": False,
                    "aws_key": False,
                    "generic_secret": False,
                    "ip_address": False,
                },
            }
        )

        text = "SSN: 123-45-6789"
        result = service.redact(text)

        # No patterns enabled, so no redaction
        assert result == text

    def test_unicode_text(self):
        """Test redacting text with unicode characters."""
        service = RedactionService(
            config={
                "enabled": True,
                "patterns": {"ssn": True},
            }
        )

        text = "用户 SSN: 123-45-6789 の情報"
        result = service.redact(text)

        assert "123-45-6789" not in result
        assert "用户" in result
        assert "の情報" in result
