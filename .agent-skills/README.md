# OSI Agent Skills

Tool-agnostic skill files for working with the OSI reference implementation
and compliance suite. Each skill is a self-contained `SKILL.md` with YAML
front-matter (`name` + `description`) and Markdown instructions — the same
format used by both Cursor and Claude Code, so the files themselves work
unchanged across tools. This folder just keeps them outside any single
tool's hidden directory so it's clear they're meant to be shared.

## Available skills

| Skill | When to use |
|:---|:---|
| [`run-osi-python-tests/`](run-osi-python-tests/SKILL.md) | Run the full test pyramid (unit / property / golden / e2e / mutation / coverage / lint / typecheck / arch) for `impl/python/` and surface a single Markdown report. |
| [`run-osi-compliance/`](run-osi-compliance/SKILL.md) | Run the Foundation v0.1 compliance suite against the Python reference implementation and report per-decision (D-NNN) coverage. |

## Using these with Cursor

Cursor discovers skills under `.cursor/skills/`. Either:

1. **Symlink** (recommended — single source of truth):
   ```bash
   mkdir -p .cursor/skills
   ln -s ../../.agent-skills/run-osi-python-tests .cursor/skills/
   ln -s ../../.agent-skills/run-osi-compliance   .cursor/skills/
   ```
2. **Or copy** each skill folder into `.cursor/skills/` and remember to
   keep both in sync.

The relative `../../` paths inside each `SKILL.md` are written for the
`.agent-skills/<name>/` location. If you copy into `.cursor/skills/<name>/`
you'll need to add one more `../` to each link (or just symlink).

## Using these with Claude Code

Claude Code reads skills from `.claude/skills/` (project-scoped) or
`~/.claude/skills/` (user-scoped). Mirror the same pattern as above:

```bash
mkdir -p .claude/skills
ln -s ../../.agent-skills/run-osi-python-tests .claude/skills/
ln -s ../../.agent-skills/run-osi-compliance   .claude/skills/
```

Or for user-scoped availability across all of your projects:

```bash
ln -s "$(pwd)/.agent-skills/run-osi-python-tests" ~/.claude/skills/
ln -s "$(pwd)/.agent-skills/run-osi-compliance"   ~/.claude/skills/
```

## Using these with any other agent

The format is just `SKILL.md` with two pieces of YAML front-matter:

```yaml
---
name: skill-name
description: One-sentence summary the agent uses to decide whether to load this skill.
---
```

Followed by Markdown instructions. Most agent frameworks that support
"skills" or "rules" can ingest this format directly, or you can paste the
instructions into a system prompt.

## Authoring conventions

When adding a new skill here, keep it tool-agnostic:

- Front-matter must use `name:` (not `id:`) and `description:` (so it
  matches the Cursor / Claude Code shape).
- Instructions reference files using POSIX-style relative paths from
  the skill folder (`../../impl/python/RUNNING_TESTS.md`).
- Do not assume a specific shell, IDE, or tool integration in the
  instructions. Where tool-specific commands are necessary, list them as
  alternatives ("In Cursor: …; In Claude Code: …").
- Do not embed tool-specific UI references ("click the X button"). Skills
  are read by agents, not humans, so describe outcomes and let the agent
  pick the means.
