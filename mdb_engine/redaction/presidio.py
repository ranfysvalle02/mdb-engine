"""
Presidio Redaction Service Implementation

Provides Microsoft Presidio-based redaction using AnalyzerEngine and AnonymizerEngine.
This implementation uses ML-based entity detection for more accurate PII identification.

Requires: presidio-analyzer, presidio-anonymizer
"""

import logging
from typing import Any

from .base import BaseRedactionService, RedactionServiceError

logger = logging.getLogger(__name__)

# Optional Presidio imports
try:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.operators import Replace

    PRESIDIO_AVAILABLE = True
except ImportError:
    PRESIDIO_AVAILABLE = False
    AnalyzerEngine = None
    AnonymizerEngine = None
    Replace = None


class PresidioRedactionService(BaseRedactionService):
    """
    Microsoft Presidio-based redaction service for protecting sensitive data.

    This service uses Presidio's AnalyzerEngine and AnonymizerEngine to detect
    and redact PII entities using machine learning models.

    Supported Entities:
        - NAME: Person names
        - PHONE_NUMBER: Phone numbers
        - EMAIL_ADDRESS: Email addresses
        - CREDIT_CARD: Credit card numbers
        - SSN: Social Security Numbers
        - IP_ADDRESS: IP addresses
        - DATE_TIME: Dates and times
        - LOCATION: Geographic locations
        - And many more (see Presidio documentation)

    Configuration:
        enabled: bool - Enable/disable redaction (default: True)
        replacement: str - Replacement text (default: "[REDACTED_PII]")
        entities: list - List of entity types to detect
            (default: ["NAME", "PHONE_NUMBER", "EMAIL_ADDRESS"])
        language: str - Language code (default: "en")
        log_redactions: bool - Log redaction counts (default: True)
    """

    # Default entities to detect (conservative set)
    DEFAULT_ENTITIES = ["NAME", "PHONE_NUMBER", "EMAIL_ADDRESS"]

    def __init__(self, config: dict[str, Any] | None = None):
        """
        Initialize PresidioRedactionService with configuration.

        Args:
            config: Configuration dictionary with:
                - enabled: bool - Enable redaction (default: True)
                - replacement: str - Replacement text (default: "[REDACTED_PII]")
                - entities: list - Entity types to detect
                - language: str - Language code (default: "en")
                - log_redactions: bool - Log redaction counts (default: True)

        Raises:
            RedactionServiceError: If Presidio dependencies are not available
        """
        super().__init__(config)

        if not PRESIDIO_AVAILABLE:
            raise RedactionServiceError(
                "Presidio dependencies not available. "
                "Install with: pip install presidio-analyzer presidio-anonymizer"
            )

        # Core settings
        self.replacement = self.config.get("replacement", "[REDACTED_PII]")
        self.language = self.config.get("language", "en")
        self.log_redactions = self.config.get("log_redactions", True)

        # Entities to detect
        self.entities = self.config.get("entities", self.DEFAULT_ENTITIES)
        if not isinstance(self.entities, list):
            raise RedactionServiceError("'entities' must be a list of entity type strings")

        # Initialize Presidio engines
        try:
            self.analyzer = AnalyzerEngine()
            self.anonymizer = AnonymizerEngine()
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            OSError,
            ImportError,
        ) as e:
            raise RedactionServiceError(f"Failed to initialize Presidio engines: {e}") from e

        if self.enabled:
            logger.info(
                f"✅ PresidioRedactionService initialized with entities={self.entities}, "
                f"language='{self.language}', replacement='{self.replacement}'"
            )
        else:
            logger.info("⏸️ PresidioRedactionService initialized but DISABLED")

    def redact(self, text: str) -> str:
        """
        Redact sensitive information from text using Presidio.

        Args:
            text: Input text to redact

        Returns:
            Text with sensitive information replaced
        """
        if not self.enabled or not text:
            return text

        try:
            # Detect PII entities
            results = self.analyzer.analyze(
                text=text, entities=self.entities, language=self.language
            )

            if not results:
                return text

            # Anonymize detected entities
            anonymized_result = self.anonymizer.anonymize(
                text=text,
                analyzer_results=results,
                operators={"DEFAULT": Replace(new_value=self.replacement)},
            )

            redacted_text = anonymized_result.text

            # Log redaction summary
            if self.log_redactions:
                entity_counts: dict[str, int] = {}
                for result in results:
                    entity_type = result.entity_type
                    entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1

                total_redactions = len(results)
                logger.info(
                    f"🔒 Presidio redacted {total_redactions} PII entities: {entity_counts}"
                )

            return redacted_text

        except (
            AttributeError,
            ImportError,
            RuntimeError,
            ValueError,
            TypeError,
        ):
            logger.exception("⚠️ Presidio redaction failed")
            # Fail-safe: return original text on error
            return text

    def test_redaction(self, text: str) -> dict[str, Any]:
        """
        Test redaction on text without modifying it.

        Args:
            text: Text to test

        Returns:
            Dictionary with test results including what would be redacted
        """
        if not text:
            return {"matches": [], "would_redact": False, "entity_matches": {}}

        try:
            # Detect PII entities
            results = self.analyzer.analyze(
                text=text, entities=self.entities, language=self.language
            )

            if not results:
                return {"matches": [], "would_redact": False, "entity_matches": {}}

            # Extract matches
            matches = []
            entity_matches: dict[str, list[dict[str, Any]]] = {}

            for result in results:
                match_text = text[result.start : result.end]
                matches.append(match_text)

                entity_type = result.entity_type
                if entity_type not in entity_matches:
                    entity_matches[entity_type] = []

                entity_matches[entity_type].append(
                    {
                        "text": match_text,
                        "start": result.start,
                        "end": result.end,
                        "score": result.score,
                    }
                )

            return {
                "matches": matches,
                "would_redact": len(matches) > 0,
                "entity_matches": entity_matches,
                "redacted_preview": self.redact(text) if matches else text,
            }

        except (
            AttributeError,
            ImportError,
            RuntimeError,
            ValueError,
            TypeError,
        ) as e:
            logger.exception("⚠️ Presidio test redaction failed")
            return {
                "matches": [],
                "would_redact": False,
                "entity_matches": {},
                "error": str(e),
            }

    def get_stats(self) -> dict[str, Any]:
        """
        Get redaction service statistics.

        Returns:
            Dictionary with service stats
        """
        return {
            "provider": "presidio",
            "enabled": self.enabled,
            "replacement": self.replacement,
            "language": self.language,
            "entities": self.entities,
            "entity_count": len(self.entities),
        }
