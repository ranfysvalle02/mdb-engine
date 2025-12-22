#!/usr/bin/env python3
"""
Demo Seeding Script for ChitChat

Intelligently seeds the ChitChat example with demo data by hitting the API.
Creates users and conversations.

Usage:
    python seed_demo.py [--base-url http://localhost:8000]

Note: This file is a legacy from the taskmanager example and may need
      to be updated for the conversation app functionality.
"""

import asyncio
import aiohttp
import json
import argparse
import sys
from typing import Dict, List, Optional
from datetime import datetime, timedelta


class ChitChatDemoSeeder:
    """Intelligently seeds ChitChat with demo data via API."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self.session: Optional[aiohttp.ClientSession] = None
        self.created_users: Dict[str, Dict] = {}
        self.created_tasks: List[str] = []
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        cookies: Optional[Dict] = None
    ) -> Dict:
        """Make HTTP request."""
        url = f"{self.base_url}{endpoint}"
        
        try:
            async with self.session.request(
                method,
                url,
                json=data,
                data=data,
                headers=headers or {},
                cookies=cookies,
                allow_redirects=False
            ) as response:
                if response.status == 200 or response.status == 201:
                    try:
                        return await response.json()
                    except:
                        return {"status": "success", "status_code": response.status}
                elif response.status == 302:
                    # Handle redirects (e.g., after login)
                    location = response.headers.get("Location", "")
                    return {"status": "redirect", "location": location}
                else:
                    text = await response.text()
                    return {
                        "status": "error",
                        "status_code": response.status,
                        "error": text[:200]
                    }
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def register_user(
        self,
        email: str,
        password: str,
        full_name: str
    ) -> Optional[Dict]:
        """Register a new user."""
        print(f"  📝 Registering user: {email}")
        
        # Register via API
        result = await self._request(
            "POST",
            "/register",
            data={
                "email": email,
                "password": password,
                "full_name": full_name
            }
        )
        
        if result.get("status") == "redirect" or result.get("status_code") in [200, 201, 302]:
            user_info = {
                "email": email,
                "password": password,
                "full_name": full_name
            }
            self.created_users[email] = user_info
            print(f"    ✅ User registered: {email}")
            return user_info
        else:
            print(f"    ⚠️  Registration response: {result}")
            return None
    
    async def login_user(
        self,
        email: str,
        password: str
    ) -> Optional[str]:
        """Login user and get session cookie."""
        print(f"  🔐 Logging in: {email}")
        
        result = await self._request(
            "POST",
            "/login",
            data={
                "email": email,
                "password": password
            }
        )
        
        # For login, we need to handle cookies
        # In a real scenario, we'd extract cookies from the response
        # For now, we'll just verify login worked
        if result.get("status") == "redirect" or result.get("status_code") in [200, 201, 302]:
            print(f"    ✅ Login successful: {email}")
            return "session_cookie"  # Placeholder
        else:
            print(f"    ⚠️  Login response: {result}")
            return None
    
    async def create_task_via_api(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        status: str = "pending"
    ) -> Optional[str]:
        """Create a task via API."""
        result = await self._request(
            "POST",
            "/api/tasks",
            data={
                "title": title,
                "description": description,
                "priority": priority,
                "status": status
            }
        )
        
        if result.get("success") or result.get("id"):
            task_id = result.get("id") or "unknown"
            self.created_tasks.append(task_id)
            return task_id
        return None
    
    async def create_task_via_ai(
        self,
        message: str
    ) -> List[str]:
        """Create tasks using AI chat API."""
        print(f"  🤖 Creating tasks via AI: '{message[:50]}...'")
        
        result = await self._request(
            "POST",
            "/api/ai/chat",
            data={"message": message}
        )
        
        created_task_ids = []
        if result.get("created_tasks"):
            for task in result.get("created_tasks", []):
                task_id = task.get("_id") or task.get("id")
                if task_id:
                    created_task_ids.append(task_id)
                    self.created_tasks.append(task_id)
            print(f"    ✅ AI created {len(created_task_ids)} task(s)")
        else:
            print(f"    ⚠️  AI response: {result.get('response', 'No response')[:100]}")
        
        return created_task_ids
    
    async def analyze_tasks(self) -> Dict:
        """Get AI analysis of tasks."""
        print(f"  📊 Analyzing tasks")
        
        result = await self._request(
            "POST",
            "/api/ai/analyze"
        )
        
        if result.get("insights"):
            print(f"    ✅ Analysis complete: {len(result.get('insights', []))} insights")
        else:
            print(f"    ⚠️  Analysis response: {result}")
        
        return result
    
    async def seed_all(self):
        """Seed demo data."""
        print("🌱 Starting ChitChat Demo Seeding")
        print("=" * 60)
        
        # Create users
        print("\n👥 Creating Users:")
        users = [
            {
                "email": "alice@example.com",
                "password": "demo123",
                "full_name": "Alice Developer"
            },
            {
                "email": "bob@example.com",
                "password": "demo123",
                "full_name": "Bob Manager"
            },
            {
                "email": "charlie@example.com",
                "password": "demo123",
                "full_name": "Charlie Designer"
            }
        ]
        
        for user in users:
            await self.register_user(
                email=user["email"],
                password=user["password"],
                full_name=user["full_name"]
            )
            await asyncio.sleep(0.5)  # Small delay between requests
        
        # Login first user (for session context)
        if users:
            await self.login_user(
                email=users[0]["email"],
                password=users[0]["password"]
            )
        
        # Create manual tasks
        print("\n📋 Creating Manual Tasks:")
        tasks = [
            {
                "title": "Set up CI/CD pipeline",
                "description": "Configure GitHub Actions for automated testing and deployment",
                "priority": "high",
                "status": "in_progress"
            },
            {
                "title": "Review pull requests",
                "description": "Code review for pending PRs",
                "priority": "medium",
                "status": "pending"
            },
            {
                "title": "Design new brand identity",
                "description": "Create logo, color palette, and typography guidelines",
                "priority": "high",
                "status": "pending"
            },
            {
                "title": "Update product descriptions",
                "description": "Refresh SEO-optimized descriptions for top 20 products",
                "priority": "medium",
                "status": "pending"
            }
        ]
        
        for task in tasks:
            task_id = await self.create_task_via_api(
                title=task["title"],
                description=task.get("description", ""),
                priority=task.get("priority", "medium"),
                status=task.get("status", "pending")
            )
            if task_id:
                print(f"    ✅ Created: {task['title']}")
            await asyncio.sleep(0.3)
        
        # Create tasks via AI
        print("\n🤖 Creating Tasks via AI:")
        ai_messages = [
            "I need to finish the quarterly report by Friday and prepare the presentation for the board meeting",
            "Plan the team offsite for next month - we need venue, activities, and catering",
            "Create tasks for the social media campaign launch - we need content calendar, asset creation, and scheduling"
        ]
        
        for message in ai_messages:
            await self.create_task_via_ai(message=message)
            await asyncio.sleep(1.0)  # Longer delay for AI calls
        
        # Get analysis
        if tasks or ai_messages:
            print("\n📊 Getting AI Analysis:")
            await self.analyze_tasks()
            await asyncio.sleep(0.5)
        
        # Summary
        print("\n" + "=" * 60)
        print("🎉 Demo Seeding Complete!")
        print("=" * 60)
        print(f"\n📊 Summary:")
        print(f"   - Users: {len(self.created_users)}")
        print(f"   - Tasks: {len(self.created_tasks)}")
        
        print(f"\n🔗 Access the demo:")
        print(f"   - Dashboard: {self.base_url}/dashboard")
        
        print(f"\n👤 Demo Users (password: demo123):")
        for email, user in self.created_users.items():
            print(f"   - {email}")
        
        print(f"\n💡 Try the AI features:")
        print(f"   - Click the 🤖 button in the dashboard")
        print(f"   - Try: 'I need to review all pending tasks and prioritize them'")
        print(f"   - Or: 'Analyze my tasks and suggest what to work on next'")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Seed ChitChat with demo data")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the ChitChat API (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--skip-ai",
        action="store_true",
        help="Skip AI-generated tasks (faster, but less interesting)"
    )
    
    args = parser.parse_args()
    
    # Check if server is running
    print(f"🔍 Checking if server is running at {args.base_url}...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{args.base_url}/", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status not in [200, 302]:
                    print(f"⚠️  Server returned status {resp.status}")
                    print("   Make sure ChitChat is running!")
                    sys.exit(1)
    except aiohttp.ClientError as e:
        print(f"❌ Cannot connect to server at {args.base_url}")
        print(f"   Error: {e}")
        print("\n💡 Make sure ChitChat is running:")
        print("   docker-compose up")
        print("   OR")
        print("   python web.py")
        sys.exit(1)
    
    print("✅ Server is running!\n")
    
    # Seed demo data
    async with ChitChatDemoSeeder(base_url=args.base_url) as seeder:
        await seeder.seed_all()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Seeding interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error during seeding: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
