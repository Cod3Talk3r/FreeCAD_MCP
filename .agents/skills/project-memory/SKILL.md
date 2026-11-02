
---
name: project-memory
description: Create, maintain, retrieve and update persistent shared project memory for coding agents. Use when starting work on an existing project, completing significant changes, recording architectural decisions, switching agents or preparing project handoffs.
---

# Project Memory

Maintain a concise, accurate and shared memory of the current software project.

The memory is stored in PROJECT_MEMORY/ and is shared by all coding agents working in the same repository.

## 1. Memory Structure

Use this structure:

PROJECT_MEMORY/
├── INDEX.md
├── PROJECT.md
├── CURRENT_STATE.md
├── ARCHITECTURE.md
├── DECISIONS.md
├── TESTING.md
└── CHANGELOG.md

Do not create additional files unless they provide clear value.

## 2. Starting a Task

Before exploring the repository:

1. Read PROJECT_MEMORY/INDEX.md.
2. Read PROJECT_MEMORY/CURRENT_STATE.md.
3. Read PROJECT_MEMORY/PROJECT.md if project goals are unclear.
4. Read ARCHITECTURE.md only when architecture is relevant.
5. Read DECISIONS.md when a design decision is involved.
6. Read TESTING.md when verification is required.
7. Read CHANGELOG.md only when historical changes matter.

Do not read every memory file by default.

Verify critical assumptions against the actual source code.

If memory is missing or incomplete, inspect only the relevant parts of the repository.

## 3. Initializing Memory

If PROJECT_MEMORY/ does not exist or is incomplete:

1. Inspect the repository structure.
2. Read README and project configuration.
3. Identify the main entry points and important modules.
4. Identify major dependencies and technologies.
5. Inspect existing tests and test commands.
6. Inspect recent Git history when useful.
7. Create the memory files.
8. Separate verified facts from assumptions.
9. Keep the initial documentation concise.

Do not attempt to document every source file.

## 4. Updating Memory After Work

After completing a meaningful task:

### CURRENT_STATE.md

Update:
- Current implementation status.
- Completed work.
- Remaining work.
- Known bugs and limitations.
- Relevant branch and commit information.

Keep this file focused on the current state, not the entire project history.

### ARCHITECTURE.md

Update only when architecture, module responsibilities, interfaces or dependencies change.

### DECISIONS.md

Record significant technical decisions.

For each decision include:
- Date.
- Decision.
- Reason.
- Alternatives considered when relevant.
- Consequences.

Do not record trivial implementation details.

### TESTING.md

Record:
- Important test commands.
- Relevant test results.
- Environment requirements.
- Known testing limitations.

Never record unverified test results as successful.

### CHANGELOG.md

Record meaningful project changes.

Include relevant commit hashes when available.

### INDEX.md

Maintain a concise index describing the purpose of each memory file and when it should be read.

## 5. Memory Quality

- Keep information concise and factual.
- Prefer updating existing information over duplication.
- Remove obsolete information.
- Include dates for time-sensitive information.
- Distinguish verified facts from assumptions.
- Preserve important constraints and reasons behind decisions.
- Never store credentials, API keys, passwords or personal secrets.
- Never treat memory as more authoritative than actual source code.
- Do not store complete conversations or large source-code blocks.

## 6. Multi-Agent Collaboration

All agents must use the same PROJECT_MEMORY directory.

Before updating a memory file:
1. Inspect the current Git status.
2. Read the latest version of the target file.
3. Check for changes made by other agents.
4. Merge new information instead of blindly replacing content.

When possible:
- Keep changes small.
- Append dated decisions instead of rewriting historical records.
- Avoid simultaneous modifications to the same memory file.
- Never discard another agent's uncommitted changes.
- Report unresolved conflicts instead of guessing.

## 7. Git Awareness

- Record verified commit hashes.
- Do not assume uncommitted changes belong to the current task.
- Never commit or push unless explicitly requested.
- Do not overwrite user changes.
- Do not claim a change is committed without checking Git.

## 8. Handoff Between Agents

When another agent needs to continue the project:

1. Update CURRENT_STATE.md.
2. Record completed work and verification.
3. Record remaining tasks.
4. Mention relevant files and modules.
5. Include known limitations.
6. Provide a concise handoff summary.

The next agent must be able to continue without reading previous conversations.

For handoffs between OpenCode and Kilo, apply the same procedure: both tools read
the same `PROJECT_MEMORY/` directory, so keep updates small and factual, and make
`CURRENT_STATE.md` sufficient for the next agent to continue without prior context.

## 9. Token Efficiency

- Read only relevant memory files.
- Keep INDEX.md and CURRENT_STATE.md especially concise.
- Avoid duplicating information between files.
- Prefer references to source paths over copying source code.
- Do not update memory after every trivial operation.
- Update memory after meaningful changes, completed tasks or important discoveries.

## 10. Final Check

Before finishing:

- Confirm memory reflects the actual project state.
- Confirm test results are accurate.
- Confirm no important decision was lost.
- Report which memory files were updated.
- If nothing meaningful changed, do not create unnecessary updates.

## 11. Completion Claims

- Never claim a task is complete based only on an agent's intention or an
  unverified report; verify against source code and tests before reporting done.
