---
name: cadence-adr
description: Create the next-numbered Architecture Decision Record in the project's docs/ADR/ directory, using the Nygard format (Status, Context, Decision, Consequences). Use when the user runs /cadence-adr, asks to write an ADR, document an architectural decision, or capture a judgment call.
argument-hint: "<title>"
---

# /cadence-adr

You are creating a new Architecture Decision Record in the Nygard
format.

## Inputs

- Required positional: `<title>` — short descriptive title, e.g.,
  "mutation pattern" or "use Redis for session cache"

## Step 1 — Determine the next number

Scan `docs/ADR/` for files matching `NNNN-*.md`. Take the highest N
seen, add 1, zero-pad to 4 digits. Use `0000-template.md` as the
template (which is N=0; the next file is N=1).

If no ADRs exist yet, the next number is `0001`.

## Step 2 — Slugify the title

Lowercase, replace spaces with hyphens, strip non-alphanumerics
except hyphens. "Mutation pattern" → `mutation-pattern`. "Use Redis
for session cache" → `use-redis-for-session-cache`.

Combine: filename is `docs/ADR/<NNNN>-<slug>.md`.

## Step 3 — Copy from template

Read `docs/ADR/0000-template.md`. Replace `# ADR-NNNN — <title>` with
the real number and title. Strip the HTML comments (they're scaffolding
for the user — they shouldn't appear in the actual ADR).

## Step 4 — Prompt the user for content

Walk through the Nygard sections interactively:

1. **Status** — default `Accepted`. Other options: `Proposed`,
   `Deprecated`, `Superseded by ADR-NNNN`. If superseded, ask for the
   replacement ADR number and note it in this one's Status line.

2. **Context** — what's the situation that forced this decision? Two
   or three short paragraphs. Push back if the answer is one
   sentence — context is the most-skipped, most-important section.

3. **Decision** — what did we decide? Lead with one sentence, then
   specifics. Encourage a code example if the decision affects how
   code is written.

4. **Consequences** — what does this make easier? What does it make
   harder? What's the migration cost? Push back if there are no
   trade-offs listed — every real decision has them.

5. **Trigger** — what prompted writing this ADR? Usually a
   retrospective entry — offer to link to the latest
   `FRAMEWORK_CHANGELOG.md` entry.

## Step 5 — Write the file

Save to `docs/ADR/<NNNN>-<slug>.md`. Do NOT overwrite if a file with
that number already exists — fail loudly instead.

## Step 6 — Update the index

Edit `docs/ADR/README.md` to add a new line under the index section:

```markdown
- [ADR-NNNN — <title>](./<NNNN>-<slug>.md)
```

Keep the index sorted by number.

## Step 7 — Suggest PATTERNS.md update

If the new ADR introduces a pattern that should be visible in
`docs/PATTERNS.md`, prompt the user: "This ADR establishes a pattern.
Should I update PATTERNS.md §N to cite it?" If yes, draft the addition
and ask for approval.

## Step 8 — Print

```
Created docs/ADR/<NNNN>-<slug>.md
Updated docs/ADR/README.md
(Optional) Updated docs/PATTERNS.md §N
```

## Safety rules

- Never reuse an ADR number, even for a superseded ADR
- Never delete an existing ADR (mark Deprecated or Superseded instead)
- Always update the index in the same operation
- If the title is too vague (e.g., "thing", "stuff"), push back and
  ask for a sharper title before creating the file
