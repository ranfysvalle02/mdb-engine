"""
Prompt templates for AI/LLM integration (Legacy from TaskManager).

Note: This file is a legacy from the taskmanager example and may not be
used in the current conversation app. The conversation app uses the LLM
service directly without these prompt templates.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime


def get_system_prompt(
    user_name: Optional[str] = None,
    task_count: int = 0,
    recent_tasks: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Generate system prompt for AI assistant.
    
    Args:
        user_name: Current user's name
        task_count: Total number of tasks
        recent_tasks: List of recent tasks (for context)
    
    Returns:
        System prompt string
    """
    context_parts = []
    
    if user_name:
        context_parts.append(f"- User: {user_name}")
    
    context_parts.append(f"- Total tasks: {task_count}")
    
    if recent_tasks:
        recent_summary = "\n".join([
            f"  - {task.get('title', 'Untitled')} ({task.get('status', 'unknown')})"
            for task in recent_tasks[:5]
        ])
        context_parts.append(f"- Recent tasks:\n{recent_summary}")
    
    context = "\n".join(context_parts) if context_parts else "No context available"
    
    return f"""You are an AI assistant for a task management application. 
You help users create, organize, and analyze tasks.

Current context:
{context}

When users ask to create tasks:
1. Parse natural language carefully
2. Extract: title, description, due date (if mentioned), priority (if mentioned)
3. Return structured task data in JSON format
4. Confirm creation in a friendly way

When analyzing tasks:
1. Review all tasks provided
2. Identify patterns and trends
3. Suggest improvements and prioritization
4. Highlight potential issues or bottlenecks

When answering general questions:
1. Be helpful and concise
2. Reference specific tasks when relevant
3. Provide actionable advice
4. Maintain context awareness

Always be professional, friendly, and focused on helping users manage their tasks effectively."""


def get_task_creation_prompt(user_message: str) -> str:
    """
    Generate prompt for parsing natural language into tasks.
    
    Args:
        user_message: User's natural language request
    
    Returns:
        Prompt string for task creation
    """
    return f"""Parse this user request into structured tasks: "{user_message}"

Extract all tasks mentioned and return a JSON array with this exact structure:
[
  {{
    "title": "Task title (required)",
    "description": "Detailed description or null",
    "due_date": "YYYY-MM-DD format or null if not mentioned",
    "priority": "low|medium|high (default: medium)",
    "status": "pending"
  }}
]

Rules:
- Extract ALL tasks mentioned, even if implicit
- If dates are mentioned (today, tomorrow, Friday, next week, etc.), convert to YYYY-MM-DD
- If priority is mentioned (urgent, important, low priority), map to low|medium|high
- If no priority mentioned, use "medium"
- Return ONLY valid JSON, no additional text
- If no tasks can be extracted, return empty array: []

Examples:
User: "I need to finish the quarterly report by Friday and review the budget"
Response: [{{"title": "Finish quarterly report", "description": null, "due_date": "2024-01-19", "priority": "high", "status": "pending"}}, {{"title": "Review budget", "description": null, "due_date": null, "priority": "medium", "status": "pending"}}]

User: "Plan the company retreat"
Response: [{{"title": "Plan the company retreat", "description": "Organize venue, activities, and logistics", "due_date": null, "priority": "medium", "status": "pending"}}]

Now parse: "{user_message}"
Return ONLY the JSON array:"""


def get_task_analysis_prompt(tasks: List[Dict[str, Any]]) -> str:
    """
    Generate prompt for analyzing tasks.
    
    Args:
        tasks: List of task dictionaries
    
    Returns:
        Prompt string for task analysis
    """
    tasks_summary = "\n".join([
        f"- {task.get('title', 'Untitled')} | Status: {task.get('status', 'unknown')} | "
        f"Priority: {task.get('priority', 'medium')} | "
        f"Due: {task.get('due_date', 'No due date') if task.get('due_date') else 'No due date'}"
        for task in tasks
    ])
    
    return f"""Analyze the following tasks and provide insights:

Tasks:
{tasks_summary}

Provide a JSON response with this structure:
{{
  "insights": [
    "Key insight 1",
    "Key insight 2"
  ],
  "suggestions": [
    "Actionable suggestion 1",
    "Actionable suggestion 2"
  ],
  "prioritized_tasks": [
    {{
      "task_title": "Task name",
      "reason": "Why this should be prioritized"
    }}
  ],
  "warnings": [
    "Potential issue 1",
    "Potential issue 2"
  ],
  "summary": "Brief overall summary"
}}

Focus on:
- Tasks that need immediate attention
- Workload distribution
- Upcoming deadlines
- Task dependencies
- Potential bottlenecks

Return ONLY valid JSON, no additional text."""


def get_task_breakdown_prompt(task_description: str) -> str:
    """
    Generate prompt for breaking down a complex task into subtasks.
    
    Args:
        task_description: Description of the complex task
    
    Returns:
        Prompt string for task breakdown
    """
    return f"""Break down this complex task into actionable subtasks: "{task_description}"

Return a JSON array of subtasks:
[
  {{
    "title": "Subtask title",
    "description": "What needs to be done",
    "estimated_order": 1,
    "priority": "low|medium|high"
  }}
]

Rules:
- Break into logical, actionable steps
- Order them in a logical sequence
- Make each subtask specific and achievable
- Consider dependencies between subtasks
- Return ONLY valid JSON array, no additional text

Example:
Task: "Plan the company retreat"
Response: [
  {{"title": "Book venue", "description": "Research and book suitable venue for retreat", "estimated_order": 1, "priority": "high"}},
  {{"title": "Send invitations", "description": "Create and send invitations to all attendees", "estimated_order": 2, "priority": "high"}},
  {{"title": "Plan activities", "description": "Organize team-building activities and schedule", "estimated_order": 3, "priority": "medium"}},
  {{"title": "Arrange catering", "description": "Coordinate meals and refreshments", "estimated_order": 4, "priority": "medium"}},
  {{"title": "Create agenda", "description": "Develop detailed agenda for the retreat", "estimated_order": 5, "priority": "low"}}
]

Now break down: "{task_description}"
Return ONLY the JSON array:"""


def get_chat_prompt(
    user_message: str,
    chat_history: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Generate prompt for general chat interactions.
    
    Args:
        user_message: Current user message
        chat_history: Previous messages in conversation
    
    Returns:
        Prompt string for chat
    """
    history_text = ""
    if chat_history:
        history_parts = []
        for msg in chat_history[-10:]:  # Last 10 messages for context
            role = msg.get("role", "user")
            content = msg.get("content", "")
            history_parts.append(f"{role.capitalize()}: {content}")
        history_text = "\n".join(history_parts)
    
    if history_text:
        return f"""Previous conversation:
{history_text}

User: {user_message}
Assistant:"""
    else:
        return f"""User: {user_message}
Assistant:"""

