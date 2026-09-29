# Ideate

Find 10 small, useful open-source project ideas that don't already have a
good, maintained solution. Use WebSearch to look for real signal, not
guesses. Good sources:

- GitHub issues labeled "feature request" or "help wanted" with many
  reactions on popular repos.
- Awesome-lists with obvious gaps (a well-known list category with no good
  entry, or entries that are all abandoned).
- Claude Code / Codex skill and MCP server directories: look for a common
  task with no existing skill or MCP server.
- Hacker News and Reddit threads where someone says "I wish there was a tool
  for X" or "why doesn't this exist yet".
- Any pain points listed below under "Owner's recurring manual pain points",
  if present.

## Scope filter

Every idea must be:

- Buildable in 10-20 small, atomic tasks (one task = one commit).
- Useful on its own once built, not a toy.
- Not already solved by a maintained project with meaningful traction. If
  you're not sure, say so in the pitch rather than skipping the idea.

## Output format

Reply with **only** a JSON array, no prose before or after it, no markdown
code fence. Exactly 10 objects, each with these fields:

```json
[
  {
    "title": "Short, specific project name",
    "category": "skill | mcp | cli | devtool | data | template | agent-tool",
    "pitch": "One or two sentences: what it does and who it's for.",
    "source": "Where this idea came from (a URL, or a short description).",
    "est_scope_hours": 8
  }
]
```

`est_scope_hours` is your honest estimate of total build time in hours,
matching the 10-20 task scope above (roughly 0.5-2 hours per task).
