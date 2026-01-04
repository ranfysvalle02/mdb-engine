"""
Query validation for MongoDB Engine.

This module provides comprehensive query validation to prevent NoSQL injection,
block dangerous operators, and enforce query complexity limits.

Security Features:
- Blocks dangerous MongoDB operators ($where, $eval, $function, $accumulator)
- Prevents deeply nested queries
- Limits regex complexity to prevent ReDoS attacks
- Validates aggregation pipelines
- Prevents NoSQL injection patterns
"""

import logging
import re
from typing import Any, Dict, List, Optional, Set

from ..constants import (
    DANGEROUS_OPERATORS,
    MAX_PIPELINE_STAGES,
    MAX_QUERY_DEPTH,
    MAX_REGEX_COMPLEXITY,
    MAX_REGEX_LENGTH,
    MAX_SORT_FIELDS,
)
from ..exceptions import QueryValidationError

logger = logging.getLogger(__name__)


class QueryValidator:
    """
    Validates MongoDB queries for security and safety.

    This class provides comprehensive validation to prevent:
    - NoSQL injection attacks
    - Dangerous operator usage
    - Resource exhaustion via complex queries
    - ReDoS attacks via complex regex patterns
    """

    def __init__(
        self,
        max_depth: int = MAX_QUERY_DEPTH,
        max_pipeline_stages: int = MAX_PIPELINE_STAGES,
        max_regex_length: int = MAX_REGEX_LENGTH,
        max_regex_complexity: int = MAX_REGEX_COMPLEXITY,
        dangerous_operators: Optional[Set[str]] = None,
    ):
        """
        Initialize the query validator.

        Args:
            max_depth: Maximum nesting depth for queries
            max_pipeline_stages: Maximum stages in aggregation pipelines
            max_regex_length: Maximum length for regex patterns
            max_regex_complexity: Maximum complexity score for regex patterns
            dangerous_operators: Set of dangerous operators to block
                (defaults to DANGEROUS_OPERATORS)
        """
        self.max_depth = max_depth
        self.max_pipeline_stages = max_pipeline_stages
        self.max_regex_length = max_regex_length
        self.max_regex_complexity = max_regex_complexity
        # Merge custom dangerous operators with defaults
        if dangerous_operators is not None:
            # Convert DANGEROUS_OPERATORS tuple to set for union operation
            default_ops = (
                set(DANGEROUS_OPERATORS)
                if isinstance(DANGEROUS_OPERATORS, tuple)
                else DANGEROUS_OPERATORS
            )
            self.dangerous_operators = default_ops | set(dangerous_operators)
        else:
            # Convert tuple to set for consistency
            self.dangerous_operators = (
                set(DANGEROUS_OPERATORS)
                if isinstance(DANGEROUS_OPERATORS, tuple)
                else DANGEROUS_OPERATORS
            )

    def validate_filter(self, filter: Optional[Dict[str, Any]], path: str = "") -> None:
        """
        Validate a MongoDB query filter.

        Args:
            filter: The query filter to validate
            path: JSON path for error reporting (used recursively)

        Raises:
            QueryValidationError: If the filter contains dangerous operators or exceeds limits
        """
        if not filter:
            return

        if not isinstance(filter, dict):
            raise QueryValidationError(
                f"Query filter must be a dictionary, got {type(filter).__name__}",
                query_type="filter",
                path=path,
            )

        # Check for dangerous operators and validate depth
        self._check_dangerous_operators(filter, path)
        self._check_query_depth(filter, path, depth=0)

    def validate_pipeline(self, pipeline: List[Dict[str, Any]]) -> None:
        """
        Validate an aggregation pipeline.

        Args:
            pipeline: The aggregation pipeline to validate

        Raises:
            QueryValidationError: If the pipeline exceeds limits or contains dangerous operators
        """
        if not pipeline:
            return

        if not isinstance(pipeline, list):
            raise QueryValidationError(
                f"Aggregation pipeline must be a list, got {type(pipeline).__name__}",
                query_type="pipeline",
            )

        # Check pipeline length
        if len(pipeline) > self.max_pipeline_stages:
            raise QueryValidationError(
                f"Aggregation pipeline exceeds maximum stages: "
                f"{len(pipeline)} > {self.max_pipeline_stages}",
                query_type="pipeline",
                context={
                    "stages": len(pipeline),
                    "max_stages": self.max_pipeline_stages,
                },
            )

        # Validate each stage
        for idx, stage in enumerate(pipeline):
            if not isinstance(stage, dict):
                raise QueryValidationError(
                    f"Pipeline stage {idx} must be a dictionary, got {type(stage).__name__}",
                    query_type="pipeline",
                    path=f"$[{idx}]",
                )

            # Check for dangerous operators in each stage
            stage_path = f"$[{idx}]"
            self._check_dangerous_operators(stage, stage_path)
            self._check_query_depth(stage, stage_path, depth=0)

    def validate_regex(self, pattern: str, path: str = "") -> None:
        """
        Validate a regex pattern to prevent ReDoS attacks.

        Args:
            pattern: The regex pattern to validate
            path: JSON path for error reporting

        Raises:
            QueryValidationError: If the regex pattern is too complex or long
        """
        if not isinstance(pattern, str):
            return  # Not a regex pattern

        # Check length
        if len(pattern) > self.max_regex_length:
            raise QueryValidationError(
                f"Regex pattern exceeds maximum length: "
                f"{len(pattern)} > {self.max_regex_length}",
                query_type="regex",
                path=path,
                context={
                    "length": len(pattern),
                    "max_length": self.max_regex_length,
                },
            )

        # Check complexity (simple heuristic: count quantifiers and alternations)
        complexity = self._calculate_regex_complexity(pattern)
        if complexity > self.max_regex_complexity:
            raise QueryValidationError(
                f"Regex pattern exceeds maximum complexity: "
                f"{complexity} > {self.max_regex_complexity}",
                query_type="regex",
                path=path,
                context={
                    "complexity": complexity,
                    "max_complexity": self.max_regex_complexity,
                },
            )

        # Try to compile the regex to catch syntax errors early
        try:
            re.compile(pattern)
        except re.error as e:
            raise QueryValidationError(
                f"Invalid regex pattern: {e}",
                query_type="regex",
                path=path,
            ) from e

    def validate_sort(self, sort: Optional[Any]) -> None:
        """
        Validate a sort specification.

        Args:
            sort: The sort specification to validate

        Raises:
            QueryValidationError: If the sort specification exceeds limits
        """
        if not sort:
            return

        # Count sort fields
        sort_fields = self._extract_sort_fields(sort)
        if len(sort_fields) > MAX_SORT_FIELDS:
            raise QueryValidationError(
                f"Sort specification exceeds maximum fields: "
                f"{len(sort_fields)} > {MAX_SORT_FIELDS}",
                query_type="sort",
                context={
                    "fields": len(sort_fields),
                    "max_fields": MAX_SORT_FIELDS,
                },
            )

    def _check_dangerous_operators(
        self, query: Dict[str, Any], path: str = "", depth: int = 0
    ) -> None:
        """
        Recursively check for dangerous operators in a query.

        Args:
            query: The query dictionary to check
            path: Current JSON path for error reporting
            depth: Current nesting depth

        Raises:
            QueryValidationError: If a dangerous operator is found
        """
        if depth > self.max_depth:
            raise QueryValidationError(
                f"Query exceeds maximum nesting depth: {depth} > {self.max_depth}",
                query_type="filter",
                path=path,
                context={"depth": depth, "max_depth": self.max_depth},
            )

        for key, value in query.items():
            current_path = f"{path}.{key}" if path else key

            # Check if key is a dangerous operator
            if key in self.dangerous_operators:
                logger.warning(
                    f"Security: Dangerous operator '{key}' detected in query "
                    f"at path '{current_path}'"
                )
                raise QueryValidationError(
                    f"Dangerous operator '{key}' is not allowed for security reasons. "
                    f"Found at path: {current_path}",
                    query_type="filter",
                    operator=key,
                    path=current_path,
                )

            # Recursively check nested dictionaries
            if isinstance(value, dict):
                # Check for $regex operator and validate pattern
                if "$regex" in value:
                    regex_pattern = value["$regex"]
                    if isinstance(regex_pattern, str):
                        self.validate_regex(regex_pattern, f"{current_path}.$regex")
                self._check_dangerous_operators(value, current_path, depth + 1)
            elif isinstance(value, list):
                # Check list elements
                for idx, item in enumerate(value):
                    if isinstance(item, dict):
                        item_path = f"{current_path}[{idx}]"
                        # Check for $regex in list items
                        if "$regex" in item and isinstance(item["$regex"], str):
                            self.validate_regex(item["$regex"], f"{item_path}.$regex")
                        self._check_dangerous_operators(item, item_path, depth + 1)
            elif isinstance(value, str) and key == "$regex":
                # Direct $regex value (less common but possible)
                self.validate_regex(value, current_path)

    def _check_query_depth(self, query: Dict[str, Any], path: str = "", depth: int = 0) -> None:
        """
        Check query nesting depth.

        Args:
            query: The query dictionary to check
            path: Current JSON path for error reporting
            depth: Current nesting depth

        Raises:
            QueryValidationError: If query depth exceeds maximum
        """
        if depth > self.max_depth:
            raise QueryValidationError(
                f"Query exceeds maximum nesting depth: {depth} > {self.max_depth}",
                query_type="filter",
                path=path,
                context={"depth": depth, "max_depth": self.max_depth},
            )

        # Recursively check nested dictionaries
        for key, value in query.items():
            current_path = f"{path}.{key}" if path else key

            if isinstance(value, dict):
                self._check_query_depth(value, current_path, depth + 1)
            elif isinstance(value, list):
                for idx, item in enumerate(value):
                    if isinstance(item, dict):
                        item_path = f"{current_path}[{idx}]"
                        self._check_query_depth(item, item_path, depth + 1)

    def _calculate_regex_complexity(self, pattern: str) -> int:
        """
        Calculate a complexity score for a regex pattern.

        This is a simple heuristic to detect potentially dangerous regex patterns
        that could cause ReDoS attacks.

        Args:
            pattern: The regex pattern

        Returns:
            Complexity score (higher = more complex)
        """
        complexity = 0

        # Count quantifiers (can cause backtracking)
        complexity += len(re.findall(r"[*+?{]", pattern))

        # Count alternations (can cause exponential growth)
        complexity += len(re.findall(r"\|", pattern))

        # Count nested groups (can cause deep backtracking)
        complexity += len(re.findall(r"\([^)]*\([^)]*\)", pattern))

        # Count lookahead/lookbehind (can be expensive)
        complexity += len(re.findall(r"\(\?[=!<>]", pattern))

        return complexity

    def _extract_sort_fields(self, sort: Any) -> List[str]:
        """
        Extract field names from a sort specification.

        Args:
            sort: Sort specification (list of tuples, dict, or single tuple)

        Returns:
            List of field names
        """
        if isinstance(sort, list):
            return [field for field, _ in sort if isinstance(field, str)]
        elif isinstance(sort, dict):
            return list(sort.keys())
        elif isinstance(sort, tuple) and len(sort) == 2:
            return [sort[0]] if isinstance(sort[0], str) else []
        return []
