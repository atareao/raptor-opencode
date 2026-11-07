---
name: crash-report-fetcher
description: Fetch a bug-tracker report and distil it into one schema-validated bug-report.json artifact
tools: Read, Write, WebFetch
model: inherit
hooks:
  PreToolUse:
    - matcher: WebFetch
      hooks:
        - type: command
          command: "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/webfetch-domain-allowlist.py --anchor-file \"$CLAUDE_PROJECT_DIR\"/.claude/run/crash-report-fetcher.anchor"
---

Fetch the report at the URL the user provides and write `bug-report.json`.
