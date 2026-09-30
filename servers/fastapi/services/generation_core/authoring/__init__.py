"""LLM deck and slide authoring orchestration.

Moved from utils/llm_calls/author_*: these modules call composition policy,
planners, capacity recovery, batch policy and provider instrumentation in
services, so they are orchestration rather than utilities. The pure
prompt-text and parsing helpers they use stay in utils/llm_calls.
"""
