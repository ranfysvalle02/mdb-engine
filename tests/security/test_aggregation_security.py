"""
Aggregation pipeline security tests.

Verifies that dangerous pipeline stages are blocked and scoping
is correctly applied to aggregation operations.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mdb_engine.database.query_validator import QueryValidator
from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper
from mdb_engine.exceptions import QueryValidationError

# ============================================================================
# Dangerous pipeline stages ($out, $merge, $unionWith)
# ============================================================================


@pytest.mark.unit
class TestDangerousPipelineStages:
    """$out, $merge, and $unionWith must be rejected."""

    def test_out_stage_blocked(self):
        validator = QueryValidator()
        pipeline = [
            {"$match": {"status": "active"}},
            {"$out": "stolen_data"},
        ]
        with pytest.raises(QueryValidationError, match="\\$out.*not allowed"):
            validator.validate_pipeline(pipeline)

    def test_merge_stage_blocked(self):
        validator = QueryValidator()
        pipeline = [
            {"$match": {"status": "active"}},
            {"$merge": {"into": "other_collection"}},
        ]
        with pytest.raises(QueryValidationError, match="\\$merge.*not allowed"):
            validator.validate_pipeline(pipeline)

    def test_union_with_stage_blocked(self):
        validator = QueryValidator()
        pipeline = [
            {"$unionWith": {"coll": "other_tenant_data"}},
        ]
        with pytest.raises(QueryValidationError, match="\\$unionWith.*not allowed"):
            validator.validate_pipeline(pipeline)

    def test_out_as_first_stage_blocked(self):
        validator = QueryValidator()
        with pytest.raises(QueryValidationError, match="\\$out"):
            validator.validate_pipeline([{"$out": "target"}])

    def test_merge_with_complex_spec_blocked(self):
        validator = QueryValidator()
        pipeline = [
            {
                "$merge": {
                    "into": {"db": "other_db", "coll": "other_coll"},
                    "whenMatched": "replace",
                }
            },
        ]
        with pytest.raises(QueryValidationError, match="\\$merge"):
            validator.validate_pipeline(pipeline)


# ============================================================================
# Dangerous operators inside pipeline stages
# ============================================================================


@pytest.mark.unit
class TestDangerousOperatorsInPipelines:
    def test_where_in_match_stage(self):
        validator = QueryValidator()
        pipeline = [{"$match": {"$where": "this.x > 0"}}]
        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_pipeline(pipeline)

    def test_function_in_addFields(self):
        validator = QueryValidator()
        pipeline = [{"$addFields": {"computed": {"$function": {"body": "return 1"}}}}]
        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_pipeline(pipeline)

    def test_accumulator_in_group(self):
        validator = QueryValidator()
        pipeline = [{"$group": {"_id": None, "val": {"$accumulator": {"init": "0"}}}}]
        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_pipeline(pipeline)


# ============================================================================
# Pipeline limits
# ============================================================================


@pytest.mark.unit
class TestPipelineLimits:
    def test_exceeding_max_stages_rejected(self):
        validator = QueryValidator(max_pipeline_stages=5)
        pipeline = [{"$match": {"status": "a"}}] * 10
        with pytest.raises(QueryValidationError, match="exceeds maximum stages"):
            validator.validate_pipeline(pipeline)

    def test_non_list_pipeline_rejected(self):
        validator = QueryValidator()
        with pytest.raises(QueryValidationError, match="must be a list"):
            validator.validate_pipeline({"$match": {"x": 1}})  # type: ignore[arg-type]

    def test_non_dict_stage_rejected(self):
        validator = QueryValidator()
        with pytest.raises(QueryValidationError, match="must be a dictionary"):
            validator.validate_pipeline([{"$match": {"x": 1}}, "bad_stage"])  # type: ignore[list-item]


# ============================================================================
# Safe pipelines should pass
# ============================================================================


@pytest.mark.unit
class TestSafePipelines:
    def test_lookup_allowed(self):
        validator = QueryValidator()
        pipeline = [
            {
                "$lookup": {
                    "from": "users",
                    "localField": "user_id",
                    "foreignField": "_id",
                    "as": "user",
                }
            },
        ]
        validator.validate_pipeline(pipeline)

    def test_group_sort_project_allowed(self):
        validator = QueryValidator()
        pipeline = [
            {"$match": {"status": "active"}},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$project": {"category": "$_id", "count": 1}},
        ]
        validator.validate_pipeline(pipeline)

    def test_empty_pipeline_allowed(self):
        validator = QueryValidator()
        validator.validate_pipeline([])

    def test_none_pipeline_allowed(self):
        validator = QueryValidator()
        validator.validate_pipeline(None)  # type: ignore[arg-type]


# ============================================================================
# Aggregation scoping on ScopedCollectionWrapper
# ============================================================================


@pytest.mark.unit
class TestAggregationScoping:
    """Verify that aggregate prepends the scope $match stage."""

    def test_aggregate_prepends_scope_match(
        self,
        scoped_wrapper: ScopedCollectionWrapper,
        mock_raw_collection: MagicMock,
    ):
        pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
        scoped_wrapper.aggregate(pipeline)
        call_args = mock_raw_collection.aggregate.call_args
        scoped_pipeline = call_args[0][0]
        assert scoped_pipeline[0] == {"$match": {"app_id": {"$in": ["tenant_a"]}}}

    def test_aggregate_empty_pipeline_scoped(
        self,
        scoped_wrapper: ScopedCollectionWrapper,
        mock_raw_collection: MagicMock,
    ):
        scoped_wrapper.aggregate([])
        call_args = mock_raw_collection.aggregate.call_args
        scoped_pipeline = call_args[0][0]
        assert scoped_pipeline[0] == {"$match": {"app_id": {"$in": ["tenant_a"]}}}
