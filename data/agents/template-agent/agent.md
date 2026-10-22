---
name: "Template Agent"
slug: "template-agent"
description: "Reference starter agent template with Markdown-first schema"
engine: "antigravity"
model: "gemini-3.8-flash"
created_at: 2026-10-22
auth:
  provider: "antigravity"
  status: "unauthenticated"
  profile_id: "template-agent-profile"
capabilities:
  terminal: false
  file_read: true
  file_write: false
  database_read: false
schedule:
  enabled: false
  cron: "0 8 * * *"
---

# Master Prompt
You are a helpful and precise autonomous assistant.
Follow your instructions in `instructions.md` and leverage the provided tools.
Always explain your reasoning before taking actions.
