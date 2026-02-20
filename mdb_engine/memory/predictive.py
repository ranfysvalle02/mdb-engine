"""
Predictive Memory System
Counterfactuals, simulations, and future scenarios.

This module implements Predictive Memory - an AI-only superpower that allows
the system to store and validate predictions, counterfactuals, and simulations.

Examples:
- "If I explain visually, user engagement increases"
- "What would have happened if I chose differently"
- "User responds better to concise explanations on weekdays"

This enables counterfactual reasoning and learning from non-events.
"""

import logging
from datetime import datetime, timezone
from typing import Any

try:
    from pymongo.errors import OperationFailure, PyMongoError

    PYMONGO_AVAILABLE = True
except ImportError:
    raise ImportError("pip install pymongo") from None

from .base import MemoryServiceError

logger = logging.getLogger(__name__)


class PredictiveMemoryError(MemoryServiceError):
    """Base exception for Predictive Memory errors."""

    pass


class PredictiveMemory:
    """
    Manages counterfactuals, simulations, and future scenarios.

    Predictive memory stores predictions and enables the system to:
    - Learn from what didn't happen (counterfactuals)
    - Validate predictions against reality
    - Build predictive models from experience
    - Reason about alternative futures

    Example:
        ```python
        from mdb_engine.memory.predictive import PredictiveMemory

        predictive = PredictiveMemory(collection=predictive_collection)

        # Store a prediction
        predictive.store_prediction(
            scenario="If I explain visually, user engagement increases",
            origin="simulation",
            confidence=0.65,
            scope="user",
            user_id="user123"
        )

        # Later, validate the prediction
        predictive.validate_prediction(
            prediction_id=prediction_id,
            was_correct=True
        )
        ```
    """

    def __init__(self, collection: Any):
        """
        Initialize PredictiveMemory manager.

        Args:
            collection: MongoDB collection (Motor async collection) for predictive memory
        """
        self.collection = collection
        self._indexes_ensured = False

    async def _ensure_indexes(self) -> None:
        """Create necessary indexes for predictive memory (async, idempotent)."""
        if self._indexes_ensured:
            return
        try:
            await self.collection.create_index([("scope", 1), ("validated", 1), ("confidence", -1)])
            await self.collection.create_index([("origin", 1), ("created_at", -1)])
            await self.collection.create_index("created_at")
            self._indexes_ensured = True
            logger.info("Predictive memory indexes created")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Index creation warning (may already exist): {e}")

    async def store_prediction(
        self,
        scenario: str,
        origin: str,
        confidence: float,
        validated: bool = False,
        scope: str = "user",
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        group_id: str | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
    ) -> dict[str, Any]:
        """
        Store a prediction or counterfactual scenario.

        Args:
            scenario: Description of the prediction/scenario
            origin: Origin of the prediction ("simulation", "counterfactual", "hypothesis", "pattern")
            confidence: Confidence in this prediction (0.0 to 1.0)
            validated: Whether this prediction has been validated (default: False)
            scope: Memory scope ("user", "shared", "system")
            user_id: User ID if scope is "user"
            group_id: Generic group identifier if scope is "shared"
            metadata: Optional metadata dictionary
            bucket_id: Optional bucket ID for filtering
            bucket_type: Optional bucket type

        Returns:
            Created prediction document

        Example:
            ```python
            predictive.store_prediction(
                scenario="If I explain visually, user engagement increases",
                origin="simulation",
                confidence=0.65,
                scope="user",
                user_id="user123"
            )
            ```
        """
        if not scenario or not scenario.strip():
            raise PredictiveMemoryError("Scenario text cannot be empty")

        if not (0.0 <= confidence <= 1.0):
            raise PredictiveMemoryError("Confidence must be between 0.0 and 1.0")

        try:
            await self._ensure_indexes()

            normalized_scope = "shared" if scope == "family" else scope

            prediction_doc = {
                "scenario": scenario.strip(),
                "origin": origin,
                "confidence": confidence,
                "validated": validated,
                "scope": normalized_scope,
                "created_at": datetime.now(timezone.utc),
            }

            if user_id:
                prediction_doc["user_id"] = str(user_id)
            if group_id:
                prediction_doc["group_id"] = group_id

            # Add bucket info to metadata
            final_metadata = metadata or {}
            if bucket_id:
                final_metadata["bucket_id"] = bucket_id
            if bucket_type:
                final_metadata["bucket_type"] = bucket_type
            if bucket_id and "associated_bucket_id" not in final_metadata:
                final_metadata["associated_bucket_id"] = bucket_id

            if final_metadata:
                prediction_doc["metadata"] = final_metadata

            result = await self.collection.insert_one(prediction_doc)
            prediction_doc["_id"] = result.inserted_id

            logger.info(f"Prediction stored: confidence={confidence:.2f}, origin={origin}")

            return prediction_doc

        except (PyMongoError, OperationFailure) as e:
            logger.error(f"Failed to store prediction: {e}", exc_info=True)
            raise PredictiveMemoryError(f"Failed to store prediction: {e}") from e

    async def validate_prediction(
        self,
        prediction_id: str,
        was_correct: bool,
        notes: str | None = None,
    ) -> bool:
        """
        Validate a prediction against reality.

        Updates the prediction's validation status and tracks accuracy.

        Args:
            prediction_id: Prediction document ID
            was_correct: Whether the prediction was correct
            notes: Optional notes about the validation

        Returns:
            True if validation was successful, False otherwise

        Example:
            ```python
            predictive.validate_prediction(
                prediction_id=prediction_id,
                was_correct=True,
                notes="User engagement increased by 30% when using visual explanations"
            )
            ```
        """
        try:
            from bson import ObjectId

            update_doc = {
                "$set": {
                    "validated": True,
                    "was_correct": was_correct,
                    "validated_at": datetime.now(timezone.utc),
                }
            }

            if notes:
                update_doc["$set"]["validation_notes"] = notes

            # Track validation history (capped to prevent unbounded growth)
            update_doc["$push"] = {
                "validation_history": {
                    "$each": [
                        {
                            "was_correct": was_correct,
                            "notes": notes,
                            "timestamp": datetime.now(timezone.utc),
                        }
                    ],
                    "$slice": -100,
                }
            }

            result = await self.collection.update_one({"_id": ObjectId(prediction_id)}, update_doc)

            if result.modified_count > 0:
                status = "correct" if was_correct else "incorrect"
                logger.info(f"Prediction validated: {prediction_id} → {status}")
                return True
            return False

        except (PyMongoError, OperationFailure, ValueError) as e:
            logger.warning(f"Failed to validate prediction: {e}")
            return False

    async def get_predictions(
        self,
        scope: str = "user",
        user_id: str | None = None,
        validated: bool | None = None,
        min_confidence: float = 0.5,
        limit: int = 10,
        origin: str | None = None,
        group_id: str | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve predictions matching criteria.

        Args:
            scope: Memory scope to filter by ("user", "shared", "system")
            user_id: User ID if scope is "user"
            group_id: Generic group identifier if scope is "shared"
            validated: Filter by validation status (None = all, True = validated, False = unvalidated)
            min_confidence: Minimum confidence threshold (default: 0.5)
            limit: Maximum number of results (default: 10)
            origin: Optional origin filter
            bucket_id: Optional bucket ID to filter by
            bucket_type: Optional bucket type to filter by

        Returns:
            List of prediction documents, sorted by confidence (descending)

        Example:
            ```python
            predictions = predictive.get_predictions(
                scope="user",
                user_id="user123",
                validated=False,  # Get unvalidated predictions
                min_confidence=0.6,
                bucket_id="category:CODE:user123"
            )
            ```
        """
        try:
            normalized_scope = "shared" if scope == "family" else scope

            query = {
                "scope": normalized_scope,
                "confidence": {"$gte": min_confidence},
            }

            if user_id:
                query["user_id"] = str(user_id)
            if group_id:
                query["group_id"] = group_id
            if validated is not None:
                query["validated"] = validated
            if origin:
                query["origin"] = origin

            # Add bucket filtering
            if bucket_id:
                query["metadata.associated_bucket_id"] = bucket_id
            elif bucket_type:
                query["metadata.bucket_type"] = bucket_type

            cursor = self.collection.find(query).sort([("confidence", -1), ("created_at", -1)]).limit(limit)

            predictions = await cursor.to_list(length=limit)
            logger.debug(f"Retrieved {len(predictions)} predictions " f"(scope: {scope}, validated: {validated})")
            return predictions

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get predictions: {e}")
            return []

    async def get_prediction_accuracy(
        self,
        scope: str = "user",
        user_id: str | None = None,
        origin: str | None = None,
    ) -> dict[str, Any]:
        """
        Get accuracy statistics for validated predictions.

        Args:
            scope: Memory scope
            user_id: User ID if scope is "user"
            origin: Optional origin filter

        Returns:
            Dictionary with accuracy statistics
        """
        try:
            query = {"scope": scope, "validated": True}
            if user_id:
                query["user_id"] = str(user_id)
            if origin:
                query["origin"] = origin

            total_validated = await self.collection.count_documents(query)
            correct = await self.collection.count_documents({**query, "was_correct": True})
            incorrect = await self.collection.count_documents({**query, "was_correct": False})

            accuracy = (correct / total_validated * 100) if total_validated > 0 else 0.0

            return {
                "total_validated": total_validated,
                "correct": correct,
                "incorrect": incorrect,
                "accuracy_percentage": accuracy,
            }

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get prediction accuracy: {e}")
            return {"total_validated": 0, "accuracy_percentage": 0.0, "error": str(e)}
