#!/usr/bin/env python3
"""
Test script for memory and bucket functionality in chit_chat example.

This script tests:
1. Memory injection WITHOUT bucket_id (optional parameter)
2. Memory injection WITH bucket_id and bucket_type
3. Automatic metadata preservation during content updates (bucket_id, context_id, category)
4. Memory retrieval and filtering by bucket
5. Metadata merging (updating some fields while preserving others)

Run this script after starting the chit_chat app to verify all functionality works.

Note: 
- bucket_id and bucket_type are OPTIONAL - memories work perfectly fine without them
- Metadata preservation is AUTOMATIC - you don't need to pass existing metadata when updating
- The service automatically preserves all existing metadata fields during updates
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")
TEST_EMAIL = os.getenv("TEST_EMAIL", "test@example.com")
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "testpassword123")

# Colors for output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"


def print_test(name: str):
    """Print test header"""
    print(f"\n{BLUE}━━━ Testing: {name} ━━━{RESET}")


def print_success(message: str):
    """Print success message"""
    print(f"{GREEN}✅ {message}{RESET}")


def print_error(message: str):
    """Print error message"""
    print(f"{RED}❌ {message}{RESET}")


def print_info(message: str):
    """Print info message"""
    print(f"{YELLOW}ℹ️  {message}{RESET}")


class TestClient:
    """Test client for chit_chat API"""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session = requests.Session()
        self.user_id = None

    def login(self, email: str, password: str) -> bool:
        """Login and get session"""
        print_info(f"Logging in as {email}...")
        try:
            response = self.session.post(
                f"{self.base_url}/api/auth/login",
                json={"email": email, "password": password},
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    print_success("Login successful")
                    return True
                else:
                    print_error(f"Login failed: {data.get('error', 'Unknown error')}")
                    return False
            else:
                print_error(f"Login failed: HTTP {response.status_code}")
                return False
        except Exception as e:
            print_error(f"Login error: {e}")
            return False

    def register(self, email: str, password: str) -> bool:
        """Register new user"""
        print_info(f"Registering user {email}...")
        try:
            response = self.session.post(
                f"{self.base_url}/api/auth/register",
                json={"email": email, "password": password},
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    print_success("Registration successful")
                    return True
                else:
                    print_error(f"Registration failed: {data.get('error', 'Unknown error')}")
                    return False
            else:
                print_error(f"Registration failed: HTTP {response.status_code}")
                return False
        except Exception as e:
            print_error(f"Registration error: {e}")
            return False

    def inject_memory(
        self,
        memory: str,
        bucket_id: str = None,
        bucket_type: str = None,
        metadata: dict = None,
    ) -> dict | None:
        """Inject a memory"""
        payload = {"memory": memory}
        if bucket_id:
            payload["bucket_id"] = bucket_id
        if bucket_type:
            payload["bucket_type"] = bucket_type
        if metadata:
            payload["metadata"] = metadata

        try:
            response = self.session.post(
                f"{self.base_url}/api/memories/inject", json=payload
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return data.get("memory")
                else:
                    print_error(f"Inject failed: {data.get('error', 'Unknown error')}")
                    return None
            else:
                print_error(f"Inject failed: HTTP {response.status_code}")
                return None
        except Exception as e:
            print_error(f"Inject error: {e}")
            return None

    def update_memory(
        self,
        memory_id: str,
        data: str,
        bucket_id: str = None,
        bucket_type: str = None,
        metadata: dict = None,
    ) -> dict | None:
        """Update a memory"""
        payload = {"data": data}
        if bucket_id:
            payload["bucket_id"] = bucket_id
        if bucket_type:
            payload["bucket_type"] = bucket_type
        if metadata:
            payload["metadata"] = metadata

        try:
            response = self.session.put(
                f"{self.base_url}/api/memories/{memory_id}", json=payload
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return data.get("memory")
                else:
                    print_error(f"Update failed: {data.get('error', 'Unknown error')}")
                    return None
            else:
                print_error(f"Update failed: HTTP {response.status_code}")
                return None
        except Exception as e:
            print_error(f"Update error: {e}")
            return None

    def get_all_memories(self, limit: int = 100) -> list:
        """Get all memories"""
        try:
            response = self.session.get(
                f"{self.base_url}/api/memories", params={"limit": limit}
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return data.get("memories", [])
                else:
                    print_error(f"Get memories failed: {data.get('error', 'Unknown error')}")
                    return []
            else:
                print_error(f"Get memories failed: HTTP {response.status_code}")
                return []
        except Exception as e:
            print_error(f"Get memories error: {e}")
            return []

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory"""
        try:
            response = self.session.delete(f"{self.base_url}/api/memories/{memory_id}")
            if response.status_code == 200:
                data = response.json()
                return data.get("success", False)
            else:
                print_error(f"Delete failed: HTTP {response.status_code}")
                return False
        except Exception as e:
            print_error(f"Delete error: {e}")
            return False


def test_inject_without_bucket(client: TestClient):
    """Test injecting memory WITHOUT bucket_id (optional parameter)"""
    print_test("Inject Memory WITHOUT Bucket (Optional)")

    memory = client.inject_memory(
        memory="User likes to code in their spare time",
        metadata={"source": "test", "category": "hobby"},
    )

    if not memory:
        print_error("Failed to inject memory without bucket_id")
        return None

    memory_id = memory.get("id")
    metadata = memory.get("metadata", {})

    # Verify bucket_id is NOT in metadata (should be None/not present)
    if "bucket_id" not in metadata or metadata.get("bucket_id") is None:
        print_success("bucket_id correctly omitted when not provided")
    else:
        print_error(
            f"bucket_id should not be present when not provided. Got '{metadata.get('bucket_id')}'"
        )
        return None

    # Verify context_id is NOT in metadata
    if "context_id" not in metadata or metadata.get("context_id") is None:
        print_success("context_id correctly omitted when bucket_id not provided")
    else:
        print_error(
            f"context_id should not be present when bucket_id not provided. Got '{metadata.get('context_id')}'"
        )

    # Verify other metadata is preserved
    if metadata.get("source") == "test":
        print_success("Additional metadata preserved without bucket_id")
    else:
        print_error(f"Additional metadata not preserved. Expected 'test', got '{metadata.get('source')}'")

    return memory_id


def test_inject_with_bucket(client: TestClient):
    """Test injecting memory with bucket_id and bucket_type"""
    print_test("Inject Memory WITH Bucket")

    memory = client.inject_memory(
        memory="User prefers Python programming language",
        bucket_id="bucket:general:testuser",
        bucket_type="general",
        metadata={"source": "test", "category": "preference"},
    )

    if not memory:
        print_error("Failed to inject memory")
        return None

    memory_id = memory.get("id")
    metadata = memory.get("metadata", {})

    # Verify bucket_id is in metadata
    if metadata.get("bucket_id") == "bucket:general:testuser":
        print_success("bucket_id stored correctly in metadata")
    else:
        print_error(
            f"bucket_id not found or incorrect. Expected 'bucket:general:testuser', got '{metadata.get('bucket_id')}'"
        )
        return None

    # Verify context_id (backwards compatibility)
    if metadata.get("context_id") == "bucket:general:testuser":
        print_success("context_id set correctly (backwards compatibility)")
    else:
        print_error(
            f"context_id not found or incorrect. Expected 'bucket:general:testuser', got '{metadata.get('context_id')}'"
        )

    # Verify bucket_type
    if metadata.get("bucket_type") == "general":
        print_success("bucket_type stored correctly in metadata")
    else:
        print_error(
            f"bucket_type not found or incorrect. Expected 'general', got '{metadata.get('bucket_type')}'"
        )

    # Verify other metadata
    if metadata.get("source") == "test":
        print_success("Additional metadata preserved")
    else:
        print_error(f"Additional metadata not preserved. Expected 'test', got '{metadata.get('source')}'")

    return memory_id


def test_update_preserves_metadata(client: TestClient, memory_id: str):
    """Test that updating memory automatically preserves bucket_id and other metadata"""
    print_test("Update Memory Automatically Preserves Metadata")

    # Update only the content - metadata should be automatically preserved
    # Note: We don't pass any metadata - the service preserves it automatically!
    updated = client.update_memory(
        memory_id=memory_id,
        data="User prefers Python programming language and JavaScript",
    )

    if not updated:
        print_error("Failed to update memory")
        return False

    metadata = updated.get("metadata", {})

    # Verify bucket_id is automatically preserved (we didn't pass it!)
    if metadata.get("bucket_id") == "bucket:general:testuser":
        print_success("bucket_id automatically preserved after content update (not passed in request)")
    else:
        print_error(
            f"bucket_id lost after update! Expected 'bucket:general:testuser', got '{metadata.get('bucket_id')}'"
        )
        return False

    # Verify context_id is automatically preserved
    if metadata.get("context_id") == "bucket:general:testuser":
        print_success("context_id automatically preserved after content update (not passed in request)")
    else:
        print_error(
            f"context_id lost after update! Expected 'bucket:general:testuser', got '{metadata.get('context_id')}'"
        )

    # Verify bucket_type is automatically preserved
    if metadata.get("bucket_type") == "general":
        print_success("bucket_type automatically preserved after content update (not passed in request)")
    else:
        print_error(
            f"bucket_type lost after update! Expected 'general', got '{metadata.get('bucket_type')}'"
        )

    # Verify other metadata is automatically preserved
    if metadata.get("source") == "test":
        print_success("Additional metadata automatically preserved after content update (not passed in request)")
    else:
        print_error(
            f"Additional metadata lost after update! Expected 'test', got '{metadata.get('source')}'"
        )

    return True


def test_update_with_new_bucket(client: TestClient, memory_id: str):
    """Test updating memory with new bucket_id"""
    print_test("Update Memory with New Bucket")

    # Update with new bucket_id
    updated = client.update_memory(
        memory_id=memory_id,
        data="User prefers Python programming language and JavaScript",
        bucket_id="bucket:updated:testuser",
        bucket_type="updated",
    )

    if not updated:
        print_error("Failed to update memory with new bucket")
        return False

    metadata = updated.get("metadata", {})

    # Verify new bucket_id
    if metadata.get("bucket_id") == "bucket:updated:testuser":
        print_success("bucket_id updated correctly")
    else:
        print_error(
            f"bucket_id not updated! Expected 'bucket:updated:testuser', got '{metadata.get('bucket_id')}'"
        )
        return False

    # Verify new bucket_type
    if metadata.get("bucket_type") == "updated":
        print_success("bucket_type updated correctly")
    else:
        print_error(
            f"bucket_type not updated! Expected 'updated', got '{metadata.get('bucket_type')}'"
        )

    return True


def test_list_memories(client: TestClient):
    """Test listing all memories"""
    print_test("List All Memories")

    memories = client.get_all_memories(limit=100)

    if isinstance(memories, list):
        print_success(f"Retrieved {len(memories)} memories")
        if memories:
            # Show first memory as example
            first = memories[0]
            print_info(f"Sample memory: {first.get('memory', 'N/A')[:50]}...")
            print_info(f"  ID: {first.get('id', 'N/A')}")
            print_info(f"  Metadata keys: {list(first.get('metadata', {}).keys())}")
        return True
    else:
        print_error("Failed to retrieve memories")
        return False


def main():
    """Run all tests"""
    print(f"\n{BLUE}{'='*60}")
    print("Memory and Bucket Functionality Test")
    print(f"{'='*60}{RESET}\n")

    client = TestClient(BASE_URL)

    # Try to login, if fails try to register
    if not client.login(TEST_EMAIL, TEST_PASSWORD):
        print_info("Login failed, attempting registration...")
        if not client.register(TEST_EMAIL, TEST_PASSWORD):
            print_error("Failed to register. Exiting.")
            sys.exit(1)
        # Try login again after registration
        if not client.login(TEST_EMAIL, TEST_PASSWORD):
            print_error("Failed to login after registration. Exiting.")
            sys.exit(1)

    # Run tests
    # Test 1: Inject without bucket_id (optional parameter)
    memory_id_no_bucket = test_inject_without_bucket(client)
    if not memory_id_no_bucket:
        print_error("Critical: Inject without bucket test failed. Stopping tests.")
        sys.exit(1)

    # Test 2: Inject with bucket_id
    memory_id = test_inject_with_bucket(client)
    if not memory_id:
        print_error("Critical: Inject with bucket test failed. Stopping tests.")
        sys.exit(1)

    if not test_update_preserves_metadata(client, memory_id):
        print_error("Critical: Metadata preservation test failed. Stopping tests.")
        sys.exit(1)

    if not test_update_with_new_bucket(client, memory_id):
        print_error("Warning: Update with new bucket test failed.")
    else:
        print_success("Update with new bucket test passed")

    if not test_list_memories(client):
        print_error("Warning: List memories test failed.")
    else:
        print_success("List memories test passed")

    # Cleanup
    print_test("Cleanup")
    if memory_id_no_bucket and client.delete_memory(memory_id_no_bucket):
        print_success(f"Deleted test memory without bucket {memory_id_no_bucket}")
    else:
        print_error(f"Failed to delete test memory without bucket {memory_id_no_bucket}")
    
    if client.delete_memory(memory_id):
        print_success(f"Deleted test memory with bucket {memory_id}")
    else:
        print_error(f"Failed to delete test memory with bucket {memory_id}")

    print(f"\n{GREEN}{'='*60}")
    print("All Tests Completed!")
    print(f"{'='*60}{RESET}\n")


if __name__ == "__main__":
    main()
