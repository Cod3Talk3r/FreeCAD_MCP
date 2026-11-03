---
name: token-saver
description: Reduce unnecessary token consumption during coding, repository exploration, debugging, testing and agent workflows. Use during software development tasks to minimize context and tool output without sacrificing correctness.
---

# Token Saver

Your objective is to minimize unnecessary API token consumption while maintaining code quality, correctness and safety.

## 1. Repository Exploration

- Inspect the project structure before exploring source code.
- Read AGENTS.md and relevant project instructions first.
- Search for symbols and references before opening files.
- Prefer targeted search and line-range reads.
- Never read the entire repository without a clear reason.
- Exclude dependencies, caches, generated files and build artifacts.
- Reuse verified information already present in project memory.
- Verify important assumptions against the actual source code.
- Run `git status` and `git diff` before editing, so existing user changes are preserved.

## 2. File Reading

- Read only files relevant to the current task.
- Avoid repeatedly reading unchanged files.
- Do not read large files from beginning to end when a relevant section is sufficient.
- Inspect related files only when necessary to understand dependencies.
- Do not copy large source files into the conversation.

## 3. Command Execution

- Prefer concise command output.
- Use quiet flags and filters when available.
- Capture exit codes.
- For test runs, show failures and summary instead of all successful output.
- Avoid rerunning successful commands without a reason.
- Combine independent, inexpensive checks when practical.
- Do not hide errors or suppress information needed for verification.
- Avoid unnecessary package installation and dependency checks.

## 4. Implementation

- Understand the relevant implementation before modifying it.
- Make the smallest correct change.
- Avoid unrelated refactoring.
- Do not implement speculative improvements.
- Avoid creating duplicate functionality.
- Do not use subagents unless parallel work has a clear benefit.
- Do not ask questions when the answer is already available in the repository.

## 5. Context Management

- Keep plans short and actionable.
- Reuse project memory instead of rediscovering established facts.
- Preserve important decisions, constraints and unresolved issues.
- Do not repeat previous explanations or large code blocks.
- Prefer concise progress reports.
- When context grows, preserve durable project knowledge in PROJECT_MEMORY.
- When context gets low, produce a concise handoff summary instead of further exploration.
- Never sacrifice important technical details just to shorten context.

## 6. Tool Usage

- Use the most targeted available tool.
- Avoid redundant shell commands.
- Do not invoke multiple tools to retrieve the same information.
- Avoid dumping complete logs, JSON responses or directory trees.
- Summarize large outputs while preserving errors and relevant evidence.

## 7. Verification

- Run targeted tests first.
- Run the full test suite when justified.
- Never claim a test passed without executing it.
- Record important verification results in project memory.
- Never skip essential tests solely to save tokens.

## 8. Final Response

Keep the final response concise.

Report:

1. What changed.
2. Tests and verification results.
3. Remaining issues.
4. Important memory updates, if any.

Do not provide lengthy explanations unless requested.
