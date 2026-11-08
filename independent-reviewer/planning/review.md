# Review of changes since HEAD

Reviewed staged, unstaged, and untracked changes across the enclosing `finally` repository, including the new independent-reviewer plugin, DeepSeek guidance, configuration, README, and revised plan. Paths below are relative to that repository root. This report is saved relative to the requested working directory, at `independent-reviewer/planning/review.md`.

## Findings

### [P1] Explicitly allow the hook to write the report

**Location:** `independent-reviewer/hooks/hooks.json:8`

The Stop hook asks `codex exec` to create `planning/review.md`, but supplies neither a write-capable sandbox setting nor an output-file option. With the documented default read-only sandbox, the review can run but cannot create or update the requested report. It works only when the user's external configuration already grants write access, making the plugin's primary behavior fail on a default installation. Set `--sandbox workspace-write` explicitly, or have Codex return the review and capture it through `--output-last-message` with the destination directory prepared. The installed CLI supports both options; `--full-auto` is no longer supported. See [OpenAI's non-interactive mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode#permissions-and-safety).

### [P2] Allow invalid actions to reach the per-action cleaning step

**Location:** `.claude/skills/deepseek/SKILL.md:61–75,94–102`; corresponding contract: `planning/PLAN.md:354`

The example constrains `side` and `action` with `Literal` and requires `quantity > 0`, then validates the entire `AssistantTurn` before cleaning it. A response containing one valid buy and one zero-quantity trade therefore raises `ValidationError` and becomes a `502`; the valid action and assistant message are discarded before the promised cleaning step runs. This contradicts the skill's instruction to drop invalid entries rather than fail the whole turn and the revised plan's instruction to drop invalid sides/actions and non-positive quantities. Validate the response envelope first, then validate and filter individual actions, or add preprocessing that removes invalid entries before nested model validation. Keep malformed envelopes as whole-turn errors.

## Verification

- Parsed all three plugin JSON files successfully.
- Checked `codex exec --help` and the official documentation for sandbox and output options.
- `git diff --check HEAD` passed.
- Reviewed the schema conflict by inspection; Pydantic is unavailable in the local Python environment. No provider calls or end-to-end hook execution were performed.

The README was updated by another process during this review; its final modified contents were inspected, rather than treating its earlier transient absence as a deletion finding.
