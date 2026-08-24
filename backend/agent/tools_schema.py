"""OpenAI-compatible tool schemas for Groq."""

from __future__ import annotations

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search the merchant catalog by keyword or category. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search text"},
                    "category": {
                        "type": "string",
                        "description": "Optional category filter",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_usual_order",
            "description": "Fetch the demo user's most recent completed order ('the usual'). Read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_proposal_from_usual",
            "description": (
                "Create a checkout proposal from the user's usual order. "
                "Includes optional growth add-on offer if within budget. "
                "Does NOT charge payment — user must confirm separately."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "stated_budget_inr": {
                        "type": "integer",
                        "description": "Max budget from user message e.g. 800",
                    },
                    "with_growth": {
                        "type": "boolean",
                        "description": "Whether to offer complementary add-on",
                        "default": True,
                    },
                },
                "required": ["user_id", "stated_budget_inr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_proposal_from_products",
            "description": "Create a proposal from explicit product IDs. Does NOT charge.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "product_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "stated_budget_inr": {"type": "integer"},
                    "with_growth": {"type": "boolean", "default": True},
                },
                "required": ["user_id", "product_ids", "stated_budget_inr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_proposal_status",
            "description": "Get current proposal details, total, and status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                },
                "required": ["proposal_id"],
            },
        },
    },
]
