"""TokenCast optimizer tier (Agent SDK-backed). Depends on claude-agent-sdk.

The light tier (tokencast.py) never imports this package. This package MAY import
tokencast to reuse pricing. The two tiers otherwise communicate only via JSONL files.
"""
