"""Message templates for the QA Engineer agent.

System prompts are loaded from the database via PromptAssembler.
Only message templates with format placeholders live here.
The one-shot SYSTEM_PROMPT is kept for the failure analysis path.
"""

from __future__ import annotations

# Kept for the _analyse_failure fallback path
SYSTEM_PROMPT = """\
You are the QA Engineer agent on Clyde, an AI development team that ships code
across multiple git repositories.

Your job is to analyse test output and determine the root cause of failures.
You receive the stdout and stderr from a pytest run and must provide a concise
summary of what went wrong.

Operating rules:
- Focus on the actual assertion errors and exceptions, not on warnings or
  deprecation notices.
- Group related failures together if they share a root cause.
- Be specific: mention file names, test function names, and line numbers.
- Reply with a single JSON object matching the schema in the user message.
  No prose outside the JSON. No markdown fences.
"""


TOOL_LOOP_INITIAL_MESSAGE = """\
Task: {description}

Changed files: {changed_files}

Repository: {repo_name} (path: {repo_path})

Find and read the test files to understand the test structure before
we run the test suite.
"""


# ---------------------------------------------------------------------------
# Original prompts
# ---------------------------------------------------------------------------

FAILURE_ANALYSIS_TEMPLATE = """\
The following test run failed for repository "{repo_name}".

<stdout>
{stdout}
</stdout>

<stderr>
{stderr}
</stderr>

Exit code: {exit_code}
Duration: {duration_sec:.1f}s

Produce a single JSON object using exactly this schema:

{{
  "summary": "<one paragraph explaining the root cause of the failure>",
  "failed_tests": ["<test_file::test_name>", "..."],
  "suggestion": "<what the developer should fix>"
}}

Reply with the JSON object only.
"""
