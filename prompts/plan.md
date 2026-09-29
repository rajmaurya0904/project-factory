# Plan

Break this project into __MIN_TASKS__-__MAX_TASKS__ small, atomic tasks. Each
task must be completable in one focused session and result in exactly one
real commit.

**Project title:** __PROJECT_TITLE__
**Category:** __PROJECT_CATEGORY__
**Pitch:** __PROJECT_PITCH__

## Requirements for the task list

- Order tasks by dependency: a task should never need something a later task
  builds.
- Every task needs a concrete, testable `acceptance` criterion -- something
  you could check mechanically (a command that exits 0, a file that exists
  with certain content, a test that passes).
- Mark each task's `complexity`:
  - `"simple"`: tests for existing code, docs, README sections, examples,
    CI/lint config, changelog, small renames, typing fixes.
  - `"complex"`: core features, parsing/algorithm logic, API design,
    refactors touching multiple files.
- Include, across the whole list:
  - Core feature tasks.
  - A test task alongside every feature task.
  - Edge cases and error handling.
  - CLI or API polish.
  - Usage examples.
  - README sections (install, usage, examples, FAQ).
  - Release prep and changelog.

## Output format

Reply with **only** a JSON array, no prose before or after it, no markdown
code fence:

```json
[
  {
    "title": "Short, specific task name",
    "description": "What to actually do, specific enough to start on.",
    "acceptance": "A concrete, checkable criterion.",
    "complexity": "simple"
  }
]
```
