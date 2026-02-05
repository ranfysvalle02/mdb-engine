"""
Redaction Service for Memory Privacy Protection

This module provides a configurable redaction layer that processes text BEFORE
it reaches the LLM for fact extraction. This protects sensitive data from being
stored in memory or sent to external APIs.

Features:
- Built-in patterns for common PII (SSN, credit cards, phone, email, IP)
- Custom regex pattern support
- Allow-list for exceptions
- Configurable replacement text
- Audit logging of redactions (without exposing sensitive data)

Usage:
    redactor = RedactionService(config={
        "enabled": True,
        "replacement": "[REDACTED]",
        "patterns": {
            "ssn": True,
            "credit_card": True,
            "email": False,
            "phone": True,
            "custom": ["\\bpassword\\s*[:=]\\s*\\S+"]
        },
        "allow_list": ["support@company.com"]
    })

    clean_text = redactor.redact("My SSN is 123-45-6789")
    # Returns: "My SSN is [REDACTED]"
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class RedactionServiceError(Exception):
    """Base exception for Redaction Service failures."""

    pass


class RedactionService:
    """
    Configurable redaction service for protecting sensitive data in memory operations.

    This service processes text to remove or mask sensitive information before
    it is sent to LLMs for fact extraction or stored in the memory system.

    Built-in Patterns:
        - ssn: Social Security Numbers (XXX-XX-XXXX)
        - credit_card: Credit card numbers (13-16 digits with optional separators)
        - phone: US phone numbers (various formats)
        - email: Email addresses
        - ip_address: IPv4 addresses
        - api_key: Common API key patterns
        - password: Password assignments in text

    Configuration:
        enabled: bool - Enable/disable redaction (default: True)
        replacement: str - Replacement text (default: "[REDACTED]")
        patterns: dict - Pattern configuration (pattern_name: bool or list for custom)
        allow_list: list - Values to exclude from redaction
        log_redactions: bool - Log redaction counts (default: True)
    """

    # Built-in regex patterns for common PII
    BUILTIN_PATTERNS = {
        # Social Security Number: XXX-XX-XXXX
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        # Credit Card: 13-16 digits with optional spaces/dashes
        "credit_card": r"\b(?:\d[ -]*?){13,16}\b",
        # US Phone: Various formats including international
        "phone": r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        # Email addresses
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        # IPv4 addresses
        "ip_address": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
        # API keys: common patterns like "api_key=xxx" or "apikey: xxx"
        "api_key": (
            r"\b(?:api[_-]?key|apikey|api[_-]?secret|secret[_-]?key)"
            r"\s*[:=]\s*['\"]?[\w\-\.]+['\"]?"
        ),
        # Password assignments: "password=xxx" or "password: xxx"
        "password": r"\b(?:password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]+['\"]?",
        # Bearer tokens
        "bearer_token": r"\bBearer\s+[A-Za-z0-9\-_\.]+",
        # AWS Access Keys
        "aws_key": r"\b(?:AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}\b",
        # Generic secrets (base64-ish strings after secret/token/key)
        "generic_secret": r"\b(?:secret|token|key)\s*[:=]\s*['\"]?[A-Za-z0-9+/=]{20,}['\"]?",
    }

    # Default patterns to enable (conservative - only enable most sensitive)
    DEFAULT_ENABLED_PATTERNS = {
        "ssn": True,
        "credit_card": True,
        "phone": False,  # Often legitimate to remember
        "email": False,  # Often legitimate to remember
        "ip_address": False,
        "api_key": True,
        "password": True,
        "bearer_token": True,
        "aws_key": True,
        "generic_secret": True,
    }

    def __init__(self, config: dict[str, Any] | None = None):
        """
        Initialize RedactionService with configuration.

        Args:
            config: Configuration dictionary with:
                - enabled: bool - Enable redaction (default: True)
                - replacement: str - Replacement text (default: "[REDACTED]")
                - patterns: dict - Pattern configuration
                - allow_list: list - Values to exclude from redaction
                - log_redactions: bool - Log redaction counts (default: True)
        """
        self.config = config or {}

        # Core settings
        self.enabled = self.config.get("enabled", True)
        self.replacement = self.config.get("replacement", "[REDACTED]")
        self.log_redactions = self.config.get("log_redactions", True)

        # Allow list - values that should never be redacted
        self.allow_list = set(self.config.get("allow_list", []))

        # Build active patterns
        self._compiled_patterns: list[tuple[str, re.Pattern]] = []
        self._build_patterns()

        if self.enabled:
            logger.info(
                f"✅ RedactionService initialized with {len(self._compiled_patterns)} "
                f"active patterns, replacement='{self.replacement}'"
            )
        else:
            logger.info("⏸️ RedactionService initialized but DISABLED")

    def _build_patterns(self) -> None:
        """Build and compile regex patterns based on configuration."""
        pattern_config = self.config.get("patterns", self.DEFAULT_ENABLED_PATTERNS)

        # Process built-in patterns
        for pattern_name, pattern_regex in self.BUILTIN_PATTERNS.items():
            # Check if pattern is enabled in config (default to DEFAULT_ENABLED_PATTERNS)
            is_enabled = pattern_config.get(
                pattern_name, self.DEFAULT_ENABLED_PATTERNS.get(pattern_name, False)
            )

            if is_enabled:
                try:
                    compiled = re.compile(pattern_regex, re.IGNORECASE)
                    self._compiled_patterns.append((pattern_name, compiled))
                    logger.debug(f"📋 Enabled redaction pattern: {pattern_name}")
                except re.error as e:
                    logger.warning(f"⚠️ Invalid builtin pattern '{pattern_name}': {e}")

        # Process custom patterns
        custom_patterns = pattern_config.get("custom", [])
        if isinstance(custom_patterns, list):
            for i, pattern in enumerate(custom_patterns):
                if isinstance(pattern, str):
                    try:
                        compiled = re.compile(pattern, re.IGNORECASE)
                        self._compiled_patterns.append((f"custom_{i}", compiled))
                        logger.debug(f"📋 Enabled custom redaction pattern: custom_{i}")
                    except re.error as e:
                        logger.warning(f"⚠️ Invalid custom pattern '{pattern}': {e}")

    def redact(self, text: str) -> str:
        """
        Redact sensitive information from text.

        Args:
            text: Input text to redact

        Returns:
            Text with sensitive information replaced
        """
        if not self.enabled or not text:
            return text

        redacted_text = text
        total_redactions = 0
        redaction_counts: dict[str, int] = {}

        for pattern_name, compiled_pattern in self._compiled_patterns:
            # Find all matches
            matches = compiled_pattern.findall(redacted_text)

            # Filter out allow-listed values
            matches_to_redact = [m for m in matches if m not in self.allow_list]

            if matches_to_redact:
                # Replace matches
                for match in matches_to_redact:
                    if match in self.allow_list:
                        continue
                    redacted_text = redacted_text.replace(match, self.replacement)

                count = len(matches_to_redact)
                redaction_counts[pattern_name] = count
                total_redactions += count

        # Log redaction summary (without exposing sensitive data)
        if self.log_redactions and total_redactions > 0:
            logger.info(f"🔒 Redacted {total_redactions} sensitive items: " f"{redaction_counts}")

        return redacted_text

    def redact_dict(self, data: dict[str, Any], fields: list[str] | None = None) -> dict[str, Any]:
        """
        Redact sensitive information from dictionary values.

        Args:
            data: Dictionary to redact
            fields: Specific fields to redact (None = all string fields)

        Returns:
            Dictionary with sensitive information redacted
        """
        if not self.enabled:
            return data

        result = {}
        for key, value in data.items():
            if fields is not None and key not in fields:
                result[key] = value
            elif isinstance(value, str):
                result[key] = self.redact(value)
            elif isinstance(value, dict):
                result[key] = self.redact_dict(value, fields)
            elif isinstance(value, list):
                result[key] = [
                    self.redact(item)
                    if isinstance(item, str)
                    else self.redact_dict(item, fields)
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                result[key] = value

        return result

    def add_pattern(self, name: str, pattern: str) -> bool:
        """
        Add a custom pattern at runtime.

        Args:
            name: Pattern name for logging
            pattern: Regex pattern string

        Returns:
            True if pattern was added successfully
        """
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            self._compiled_patterns.append((name, compiled))
            logger.info(f"📋 Added runtime redaction pattern: {name}")
            return True
        except re.error as e:
            logger.warning(f"⚠️ Failed to add pattern '{name}': {e}")
            return False

    def add_to_allow_list(self, value: str) -> None:
        """
        Add a value to the allow list (will not be redacted).

        Args:
            value: Value to allow
        """
        self.allow_list.add(value)
        logger.debug(f"📋 Added to redaction allow list: {value[:20]}...")

    def remove_from_allow_list(self, value: str) -> None:
        """
        Remove a value from the allow list.

        Args:
            value: Value to remove from allow list
        """
        self.allow_list.discard(value)

    def get_stats(self) -> dict[str, Any]:
        """
        Get redaction service statistics.

        Returns:
            Dictionary with service stats
        """
        return {
            "enabled": self.enabled,
            "replacement": self.replacement,
            "active_patterns": len(self._compiled_patterns),
            "pattern_names": [name for name, _ in self._compiled_patterns],
            "allow_list_size": len(self.allow_list),
        }

    def test_redaction(self, text: str) -> dict[str, Any]:
        """
        Test redaction on text without modifying it.

        Args:
            text: Text to test

        Returns:
            Dictionary with test results including what would be redacted
        """
        if not text:
            return {"matches": [], "would_redact": False, "pattern_matches": {}}

        matches = []
        pattern_matches: dict[str, list[str]] = {}

        for pattern_name, compiled_pattern in self._compiled_patterns:
            found = compiled_pattern.findall(text)
            # Filter allow list
            found = [m for m in found if m not in self.allow_list]
            if found:
                matches.extend(found)
                pattern_matches[pattern_name] = found

        return {
            "matches": matches,
            "would_redact": len(matches) > 0,
            "pattern_matches": pattern_matches,
            "redacted_preview": self.redact(text) if matches else text,
        }


def create_redaction_service(config: dict[str, Any] | None = None) -> RedactionService:
    """
    Factory function to create a RedactionService.

    Args:
        config: Redaction configuration from manifest

    Returns:
        Configured RedactionService instance
    """
    return RedactionService(config=config)
