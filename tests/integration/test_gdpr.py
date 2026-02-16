"""
Integration tests for GDPR compliance helpers.

Tests data export, deletion, and rectification functionality.
"""

from datetime import datetime

import pytest
from bson import ObjectId


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_export_user_data_with_email(real_mongodb_engine):
    """Test user data export with email identifier."""
    engine = real_mongodb_engine
    # Create test user
    db = await engine.get_scoped_db("test_app")
    test_email = "test_export@example.com"

    # Insert test data
    await db.users.insert_one(
        {
            "email": test_email,
            "name": "Test User",
            "role": "user",
            "created_at": datetime.utcnow(),
        }
    )

    await db.user_sessions.insert_one(
        {
            "email": test_email,
            "session_id": "test_session_123",
            "created_at": datetime.utcnow(),
        }
    )

    # Export user data
    export_data = await engine.export_user_data(
        user_identifier=test_email,
        identifier_type="email",
        app_slug="test_app",
        format="json",
    )

    # Verify export structure
    assert "data" in export_data
    assert "metadata" in export_data
    assert export_data["metadata"]["format"] == "json"
    assert export_data["metadata"]["user_identifier"] == test_email
    assert export_data["metadata"]["identifier_type"] == "email"

    # Verify data was exported (collection names are prefixed with app_slug)
    assert "test_app_users" in export_data["data"]
    assert len(export_data["data"]["test_app_users"]) > 0
    assert export_data["data"]["test_app_users"][0]["email"] == test_email

    # Cleanup
    await db.users.delete_many({"email": test_email})
    await db.user_sessions.delete_many({"email": test_email})


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_export_user_data_with_user_id(real_mongodb_engine):
    """Test user data export with user_id identifier."""
    engine = real_mongodb_engine
    # Create test user
    db = await engine.get_scoped_db("test_app")
    test_user_id = str(ObjectId())

    # Insert test data
    await db.users.insert_one(
        {
            "_id": ObjectId(test_user_id),
            "email": "test@example.com",
            "name": "Test User",
        }
    )

    await db.chat_history.insert_one(
        {
            "user_id": test_user_id,
            "session_id": "test_session",
            "role": "user",
            "content": "Test message",
            "created_at": datetime.utcnow(),
        }
    )

    # Export user data
    export_data = await engine.export_user_data(
        user_identifier=test_user_id,
        identifier_type="user_id",
        app_slug="test_app",
    )

    # Verify export (collection names are prefixed with app_slug)
    assert "data" in export_data
    assert "test_app_chat_history" in export_data["data"]
    assert len(export_data["data"]["test_app_chat_history"]) > 0

    # Cleanup
    await db.users.delete_many({"_id": ObjectId(test_user_id)})
    await db.chat_history.delete_many({"user_id": test_user_id})


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_export_user_data_csv_format(real_mongodb_engine):
    """Test user data export in CSV format."""
    engine = real_mongodb_engine
    # Create test user
    db = await engine.get_scoped_db("test_app")
    test_email = "test_csv@example.com"

    await db.users.insert_one(
        {
            "email": test_email,
            "name": "Test User",
        }
    )

    # Export in CSV format
    export_data = await engine.export_user_data(
        user_identifier=test_email,
        identifier_type="email",
        format="csv",
    )

    # Verify CSV format (collection names are prefixed with app_slug when app_slug is provided)
    assert "data" in export_data
    assert export_data["metadata"]["format"] == "csv"
    # When app_slug is not provided, check for any users collection
    # When app_slug is provided, check for prefixed name
    users_key = None
    for key in export_data["data"].keys():
        if "users" in key:
            users_key = key
            break
    assert users_key is not None, f"No users collection found in {list(export_data['data'].keys())}"
    assert isinstance(export_data["data"][users_key], str)
    assert "email" in export_data["data"][users_key]

    # Cleanup
    await db.users.delete_many({"email": test_email})


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_user_data_soft_delete(real_mongodb_engine):
    """Test soft delete of user data."""
    engine = real_mongodb_engine
    # Create test user
    db = await engine.get_scoped_db("test_app")
    test_email = "test_delete@example.com"

    # Insert test data
    await db.users.insert_one(
        {
            "email": test_email,
            "name": "Test User",
            "role": "user",
        }
    )

    await db.user_sessions.insert_one(
        {
            "email": test_email,
            "session_id": "test_session",
        }
    )

    # Soft delete user data
    result = await engine.delete_user_data(
        user_identifier=test_email,
        identifier_type="email",
        app_slug="test_app",
        soft_delete=True,
    )

    # Verify deletion results
    assert "collections_processed" in result
    assert "documents_soft_deleted" in result
    assert result["documents_soft_deleted"] > 0

    # Verify data is soft deleted (still exists but marked)
    user = await db.users.find_one({"email": test_email})
    assert user is not None
    assert user.get("deleted") is True
    assert "deleted_at" in user
    assert user.get("gdpr_deleted") is True

    # Cleanup
    await db.users.delete_many({"email": test_email})
    await db.user_sessions.delete_many({"email": test_email})


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_user_data_hard_delete(real_mongodb_engine):
    """Test hard delete of user data."""
    engine = real_mongodb_engine
    # Create test user
    db = await engine.get_scoped_db("test_app")
    test_email = "test_hard_delete@example.com"

    # Insert test data
    await db.users.insert_one(
        {
            "email": test_email,
            "name": "Test User",
        }
    )

    # Hard delete user data
    result = await engine.delete_user_data(
        user_identifier=test_email,
        identifier_type="email",
        app_slug="test_app",
        soft_delete=False,
    )

    # Verify deletion results
    assert "documents_deleted" in result
    assert result["documents_deleted"] > 0

    # Verify data is actually deleted
    user = await db.users.find_one({"email": test_email})
    assert user is None


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_user_data_anonymize(real_mongodb_engine):
    """Test anonymization of user data."""
    engine = real_mongodb_engine
    # Create test user
    db = await engine.get_scoped_db("test_app")
    test_email = "test_anonymize@example.com"

    # Insert test data
    await db.users.insert_one(
        {
            "email": test_email,
            "name": "Test User",
            "password_hash": "hashed_password",
        }
    )

    # Anonymize user data
    result = await engine.delete_user_data(
        user_identifier=test_email,
        identifier_type="email",
        app_slug="test_app",
        anonymize=True,
    )

    # Verify anonymization results
    assert "documents_anonymized" in result
    assert result["documents_anonymized"] > 0

    # Verify data is anonymized
    user = await db.users.find_one({"email": {"$regex": "^deleted_"}})
    assert user is not None
    assert user.get("email").startswith("deleted_")
    assert user.get("email").endswith("@deleted.local")
    assert user.get("gdpr_anonymized") is True
    assert "anonymized_at" in user
    # Password should be removed
    assert "password_hash" not in user or user.get("password_hash") is None

    # Cleanup
    await db.users.delete_many({"gdpr_anonymized": True})


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_user_data_with_memory_service(real_mongodb_engine):
    """Test deletion includes memory service data."""
    engine = real_mongodb_engine
    # This test requires memory service to be initialized
    # For now, we'll test that the deletion doesn't fail
    # even if memory service is not available

    db = await engine.get_scoped_db("test_app")
    test_email = "test_memory@example.com"

    await db.users.insert_one(
        {
            "email": test_email,
            "name": "Test User",
        }
    )

    # Delete user data (memory service may or may not be available)
    result = await engine.delete_user_data(
        user_identifier=test_email,
        identifier_type="email",
        app_slug="test_app",
        soft_delete=True,
    )

    # Should succeed even if memory service not available
    assert "collections_processed" in result

    # Cleanup
    await db.users.delete_many({"email": test_email})


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_user_data(real_mongodb_engine):
    """Test updating user data."""
    engine = real_mongodb_engine
    # Create test user
    db = await engine.get_scoped_db("test_app")
    test_email = "test_update@example.com"
    new_email = "updated@example.com"

    # Insert test data
    await db.users.insert_one(
        {
            "email": test_email,
            "name": "Old Name",
            "role": "user",
        }
    )

    await db.user_sessions.insert_one(
        {
            "email": test_email,
            "session_id": "test_session",
        }
    )

    # Update user data
    result = await engine.update_user_data(
        user_identifier=test_email,
        updates={
            "email": new_email,
            "name": "New Name",
        },
        identifier_type="email",
        app_slug="test_app",
    )

    # Verify update results
    assert "collections_processed" in result
    assert "documents_updated" in result
    assert result["documents_updated"] > 0

    # Verify data was updated
    user = await db.users.find_one({"email": new_email})
    assert user is not None
    assert user.get("name") == "New Name"
    assert "updated_at" in user
    assert user.get("gdpr_updated") is True

    # Cleanup
    await db.users.delete_many({"email": new_email})
    await db.user_sessions.delete_many({"email": new_email})


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_export_nonexistent_user(real_mongodb_engine):
    """Test export with non-existent user."""
    engine = real_mongodb_engine
    # Export data for non-existent user
    export_data = await engine.export_user_data(
        user_identifier="nonexistent@example.com",
        identifier_type="email",
    )

    # Should return empty export
    assert "data" in export_data
    assert "metadata" in export_data
    assert export_data["metadata"]["total_documents"] == 0


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_nonexistent_user(real_mongodb_engine):
    """Test deletion with non-existent user."""
    engine = real_mongodb_engine
    # Delete data for non-existent user
    result = await engine.delete_user_data(
        user_identifier="nonexistent@example.com",
        identifier_type="email",
    )

    # Should return empty results
    assert "collections_processed" in result
    assert result["documents_deleted"] == 0
    assert result["documents_anonymized"] == 0
    assert result["documents_soft_deleted"] == 0


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_nonexistent_user(real_mongodb_engine):
    """Test update with non-existent user."""
    engine = real_mongodb_engine
    # Update data for non-existent user
    result = await engine.update_user_data(
        user_identifier="nonexistent@example.com",
        updates={"name": "New Name"},
        identifier_type="email",
    )

    # Should return empty results
    assert "collections_processed" in result
    assert result["documents_updated"] == 0


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_export_user_data_all_apps(real_mongodb_engine):
    """Test export across all apps (no app_slug specified)."""
    engine = real_mongodb_engine
    # Create test user in multiple apps
    db1 = await engine.get_scoped_db("test_app_1")
    db2 = await engine.get_scoped_db("test_app_2")
    test_email = "test_multi_app@example.com"

    await db1.users.insert_one(
        {
            "email": test_email,
            "app": "test_app_1",
        }
    )

    await db2.users.insert_one(
        {
            "email": test_email,
            "app": "test_app_2",
        }
    )

    # Export without app_slug (should find data in all apps)
    export_data = await engine.export_user_data(
        user_identifier=test_email,
        identifier_type="email",
    )

    # Should find data
    assert "data" in export_data
    assert export_data["metadata"]["total_documents"] > 0

    # Cleanup
    await db1.users.delete_many({"email": test_email})
    await db2.users.delete_many({"email": test_email})


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_user_data_app_scoped(real_mongodb_engine):
    """Test deletion scoped to specific app."""
    engine = real_mongodb_engine
    # Create test user in multiple apps
    db1 = await engine.get_scoped_db("test_app_1")
    db2 = await engine.get_scoped_db("test_app_2")
    test_email = "test_scoped@example.com"

    await db1.users.insert_one(
        {
            "email": test_email,
            "app": "test_app_1",
        }
    )

    await db2.users.insert_one(
        {
            "email": test_email,
            "app": "test_app_2",
        }
    )

    # Delete only from test_app_1
    result = await engine.delete_user_data(
        user_identifier=test_email,
        identifier_type="email",
        app_slug="test_app_1",
        soft_delete=True,
    )

    # Verify deletion
    assert result["documents_soft_deleted"] > 0

    # Verify data in test_app_1 is deleted
    user1 = await db1.users.find_one({"email": test_email})
    assert user1 is not None
    assert user1.get("deleted") is True

    # Verify data in test_app_2 is NOT deleted
    user2 = await db2.users.find_one({"email": test_email})
    assert user2 is not None
    assert user2.get("deleted") is not True

    # Cleanup
    await db1.users.delete_many({"email": test_email})
    await db2.users.delete_many({"email": test_email})


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_export_user_data_report_format(real_mongodb_engine):
    """Test export in human-readable report format."""
    engine = real_mongodb_engine
    # Create test user
    db = await engine.get_scoped_db("test_app")
    test_email = "test_report@example.com"

    await db.users.insert_one(
        {
            "email": test_email,
            "name": "Test User",
        }
    )

    # Export in report format
    export_data = await engine.export_user_data(
        user_identifier=test_email,
        identifier_type="email",
        format="report",
    )

    # Verify report format
    assert "data" in export_data
    assert "report" in export_data
    assert export_data["metadata"]["format"] == "report"
    assert isinstance(export_data["report"], str)
    assert test_email in export_data["report"]

    # Cleanup
    await db.users.delete_many({"email": test_email})


@pytest.mark.integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_user_data_error_handling(real_mongodb_engine):
    """Test that deletion handles errors gracefully."""
    engine = real_mongodb_engine
    # Create test user
    db = await engine.get_scoped_db("test_app")
    test_email = "test_error@example.com"

    await db.users.insert_one(
        {
            "email": test_email,
            "name": "Test User",
        }
    )

    # Delete user data (should handle any errors gracefully)
    result = await engine.delete_user_data(
        user_identifier=test_email,
        identifier_type="email",
        app_slug="test_app",
        soft_delete=True,
    )

    # Should have results even if some collections fail
    assert "collections_processed" in result
    assert "errors" in result

    # Cleanup
    await db.users.delete_many({"email": test_email})
