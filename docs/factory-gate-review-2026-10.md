# Factory mode: gate review, October 2026

**Status:** the decision is pending with the maintainer. The recommendation is to stop factory mode at the 2026-11-13 gate.
**Written:** 2026-10-05, after the preregistered eval `gate-001` finished.

This page records what was found, what went wrong, and the plan to the gate. The eval used private repositories with hidden tests. Its numbers, repository names and task details stay in the maintainer's private notes (see "Do not" in [NEXT.md](../NEXT.md)). This page gives the verdict in words only.

## Verdict, under the rules signed before any data

| Question | Result | What the signed rule says |
|---|---|---|
| Q1: does the factory beat a plain agent on the same tickets? | **No.** The factory scored lower than a plain agent run. | The claim "the factory beats a plain agent" is dropped. On its own this is not a stop condition. |
| Q2: does the learning loop help (rules on against rules frozen)? | **No measurable gain.** | Read as "no gain", a reading chosen before the data. The learning-loop kill condition in [FACTORY.md](FACTORY.md) is **met as written**. |
| Median cost per ticket attempt | Under $1 | The <= $20 criterion is met. |

The kill rule is an OR: a learning loop with no gain stops the project, whatever the other criteria show. The verdict is not re-read, re-scored or reopened. Any later test of the learning loop is a new question, with new money and a new preregistration, and it never rewrites `gate-001`.

## Why the factory lost (the mechanics, in words)

- **Ambiguous tickets were not built.** The spec writer asked questions instead of guessing on a large share of tickets. One scripted owner reply rarely unlocked them. A plain agent just picks a reading and gets partial credit.
- **Protected tests block correct fixes.** Where the right fix changes an existing test, the gate restores the test from the base branch, so a correct fix fails or is published unverified. Backlog item 13 predicted this.
- **A green gate is not "done".** Several merged changes passed the factory's gate but missed the ticket's real acceptance checks. The plain agent had similar misses.
- **The learning treatment was never delivered.** In the eval the loop formed no lessons at all. There were too few tickets per repository for a mistake to repeat across two distinct issues. The one class with enough repeats came from a repository whose main branch failed its own checks, which the retro plan cannot work around. So "learning on" behaved like "learning frozen".

## What went wrong in running the eval

These are recorded so they are not repeated. Each one is a validity limit. None of them reopens the verdict.

1. **No agent could run a shell, in either arm.** The local sandbox lacked `socat`, which Claude Code's sandboxed Bash needs, so every shell command failed. Agents could not run tests, lint or `verify.sh` themselves. The harness's `doctor` did not check for it. Fix: install `socat` and add a `doctor` probe.
2. **A cost miscount shrank the design.** The operator summed journal entries across restarted runs and reported about twice the real cost of one trial. The design was cut from 3 trials to 2 on that figure.
3. **The run hit the API workspace's monthly usage limit near the end.** The API answered HTTP 400, which the void rules did not list, so a few sessions were booked as model failures. The private notes show this cannot change Q2.
4. **`--trials 3` means "trial 3 only".** The documented command ran one trial instead of three. The runbook now says `--trials 1-3`.
5. **The first pilot ran on a corrupted key.** A quoting slip when copying the key into WSL stripped characters from it. `doctor --live` now checks the key with one free API call (`2fde829`).
6. **Two harness bugs appeared only under the real sandbox.** The report wrote into a path the sandbox hides, and the retro-failed record ran in a missing directory. Both are fixed: `356aa88` and `28f90f2`, the second merged after the run.

## The kill criteria, disclosed in full

- **Outside adoption.** On 2026-10-03 the criterion was relaxed from ">= 3 outside public repos" to ">= 2 public repos, own repos allowed" (`87865c7`). The first public repo, `envguard`, was created 34 minutes later. Under the original wording two of the four criteria miss. Under either wording the learning-loop condition stops the project on its own.
- **Real-repo record.** 16 of 16 agent PRs were merged. They were merged by the maintainer, mostly on repositories created the same day, with no outside review. On the two public repositories, 5 of the 6 PRs carried `cadence/verify: action_required` (the gate passed on a different tree than the commit, because guarded tests were restored). Both repositories learned one lesson: "do not modify existing files under the test root". No learned check has caught a real repeat.

## What did work

- The full pipeline runs end to end, unattended, on real GitHub repositories: issue, spec, approval, build, gate, draft PR and the learn chain.
- It is cheap: a median of about $0.5 to $1 per ticket.
- The guards held. Agents almost never weakened tests, while a plain agent often did.
- `/cadence-factory-setup` took three fresh repositories (Flutter, Python, TypeScript) to merged factory PRs.
- The DoD retry's grant path ran live (envguard, 2026-10-04).

## Plan to the gate (recommended; the maintainer decides)

| When | What | Paid API spend |
|---|---|---|
| This week | Disable the factory workflows and limit or disable their keys. Back up the release branch without touching `main`. Freeze the private eval record. | $0 |
| Weekly until approval | Check the plugin directory review. When it is approved, ship rc.3 from the prepared branch. | $0 |
| Oct 12 to 23 | Bring the generic fixes into the core plugin as new work: reading multi-line imports, and choosing a Python that has PyYAML. Reword "self-improving" to a claim the evidence supports. Ships as rc.4. | $0 |
| By Oct 23 | Publish a write-up as a preregistered negative result, hosted outside this repository. | $0 |
| 2026-11-13 | Sign the gate entry. Tag the `factory` tip `factory-final-2026-11-13` and never merge it. Remove the factory secrets and uninstall the App from every repository. | $0 |
| After the gate, optional | Re-test the learning loop with new credit, a new preregistration, and fixes for items 1 to 3 above. | about $60 to $120, later |

## Lessons for any revival

- Install `socat` and probe for it.
- Decline to run at $0 when the base branch fails its own checks (a red-main preflight).
- Let a gate step accept only added tests under guarded paths (backlog 13).
- Flag a retry whose change departs from the approved spec.
- Put CHANGELOG entries in the PR body, so parallel PRs do not conflict.
- Let a write-access user answer intake questions with a `/proceed` that the next intake round remembers.
- Count a billing-limit HTTP 400 as a void or a stop.
