import os
from typing import Optional


def get_prompt_template():
    return [
        {
            "role": "system",
            "content": """
                Use provided prompt and search results to create an elaborate and up-to-date research report in mentioned language.

                # Steps
                1. Analyze the prompt and search results.
                2. Extract topic of the report.
                3. Generate a report in markdown format.

                # Notes
                - If language is not mentioned, use language from prompt.
                - Format of report should be like *Research Report*.
                - Ignore formatting if mentioned in prompt.
            """,
        },
        {
            "role": "human",
            "content": """
                - Prompt: {prompt}
                - Language: {language}
                - Search Results: {search_results}
            """,
        },
    ]


async def get_report(query: str, language: Optional[str]):
    return "Research Report coming soon"
