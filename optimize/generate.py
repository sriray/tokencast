"""Cold-start: an LLM drafts an EvalSet from repo context. Injectable generator seam.

The default generator lazily uses the Agent SDK (excluded from unit coverage). The result is
a DRAFT for human review -- generated rule checks are LLM-authored shell commands.
"""
import json
import os

from optimize.evalset import EvalSet

_SKIP_DIRS = {"__pycache__", "node_modules", "venv", ".venv", "dist", "build"}


def gather_context(root=".", claude_md="CLAUDE.md", max_files=200):
    parts = []
    cm = os.path.join(root, claude_md)
    if os.path.isfile(cm):
        with open(cm, encoding="utf-8", errors="ignore") as fh:
            parts.append("# CLAUDE.md\n" + fh.read())
    listing = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
        for f in filenames:
            listing.append(os.path.relpath(os.path.join(dirpath, f), root))
            if len(listing) >= max_files:
                break
        if len(listing) >= max_files:
            break
    parts.append("# Files\n" + "\n".join(sorted(listing)))
    return "\n\n".join(parts)


def generate_evalset(context, generator=None):
    """generator: callable(context: str) -> dict (an eval set as a plain dict).
    Defaults to the live SDK generator. Returns an EvalSet (a DRAFT for human review)."""
    generator = generator or _default_generator
    data = generator(context)
    return EvalSet.from_dict(data)


_PROMPT = (
    "You are drafting an evaluation set for an AI coding agent working in this project.\n"
    "Return ONLY JSON of the form: {\"tasks\": [{\"id\", \"prompt\", \"pass_threshold\", "
    "\"dimensions\": [{\"name\", \"weight\", \"required\"?, "
    "\"rule\": {\"kind\": \"command|file_exists|file_contains\", ...} | \"judge\": \"rubric\"}]}]}.\n"
    "Prefer a deterministic 'tests_pass' command rule (required) plus 1-2 judge dimensions.\n\n"
    "Project context:\n"
)


def _default_generator(context):  # pragma: no cover - requires the live SDK
    import asyncio
    return asyncio.run(_generate_sdk(context))


async def _generate_sdk(context):  # pragma: no cover - requires the live SDK
    import re

    from claude_agent_sdk import query, ClaudeAgentOptions

    text = ""
    async for message in query(prompt=_PROMPT + context[:12000],
                               options=ClaudeAgentOptions(model="sonnet")):
        if type(message).__name__ == "AssistantMessage":
            for block in getattr(message, "content", []) or []:
                if type(block).__name__ == "TextBlock":
                    text += getattr(block, "text", "")
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(m.group()) if m else {"tasks": []}
