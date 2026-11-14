# Benchmark rounds

A benchmark round fixes which commits a benchmark compares and which cases they see, before they run. Its exposure ledger records what each candidate has already seen, so a later round can't reuse a case and report it as fresh.

This is about `lgcov bench`, not the gate. **A benchmark round and a gate round are different things that share a word.** `lgcov round init`, `lock` and `check` work on a benchmark round, at a path you choose, with `exposure.jsonl` next to it. `lgcov round new` and `lgcov guard` work on a [gate round](round.md), in `lgcov-round.yaml` at the repo's root, with `exposure.json`. Neither command reads the other's files.

## The round file

You choose the path, and there's no default. Two round files in one folder share one ledger.

```yaml
id: t101-163
created_at: '2026-11-07'
frozen: true
candidates:
- name: open_deep_research-163
  repo: langchain-ai/open_deep_research
  base_sha: f95d200654b5c4733e87375be15b92822c6c4ff8   # full 40-character SHAs only
  head_sha: 6532a4176a93cc9bb2102b3d825dcefa560c85d9
  locked_at: '2026-11-07'                              # written by `lgcov round lock`
cases:
- id: eu-ai-act
  source: docs/benchmark/open_deep_research-163.yaml#cases[1]
```

Unknown keys, short SHAs and repeated candidate names or case ids are refused. So is a SHA that YAML reads as a number, such as one with only digits: put it in quotes. [The T101 round](benchmark/rounds/t101-163/lgcov-round.yaml) is a full example.

## The exposure ledger

`exposure.jsonl` sits next to the round file, and rows are only ever added. There are two kinds:

```json
{"event": "lock", "round": "t101-163", "candidate": "open_deep_research-163", "repo": "langchain-ai/open_deep_research", "base_sha": "f95d200654b5c4733e87375be15b92822c6c4ff8", "head_sha": "6532a4176a93cc9bb2102b3d825dcefa560c85d9", "cases": [{"id": "eu-ai-act", "source": "docs/benchmark/open_deep_research-163.yaml#cases[1]"}], "at": "2026-11-07"}
{"event": "exposure", "round": "t101-163", "candidate": "open_deep_research-163", "repo": "langchain-ai/open_deep_research", "head_sha": "6532a4176a93cc9bb2102b3d825dcefa560c85d9", "case": "eu-ai-act", "run_id": "pilot", "seen_at": "2026-11-05"}
```

## Commands

Commit the round's files between steps:

```bash
lgcov round init --id t101-163 docs/benchmark/rounds/t101-163/lgcov-round.yaml
```

`init` writes an unfrozen template and won't overwrite a file. Fill in the candidates and cases, then commit.

```bash
lgcov round lock docs/benchmark/rounds/t101-163/lgcov-round.yaml
```

`lock` sets `frozen: true` and `locked_at`, and adds one lock row per candidate. It refuses:

- changes to any tracked file;
- an untracked file in the round's folder or below it. A round file at the repo's root makes the whole checkout its folder, so give each round its own folder;
- a round that's already frozen or locked, or that has no candidates or cases.

Untracked files elsewhere only get a warning. Commit both files after locking.

```bash
lgcov round check docs/benchmark/rounds/t101-163/lgcov-round.yaml --record run-1
```

`check` exits 0 when the round is clean and 2 otherwise, naming every problem. `--record RUN_ID` adds one exposure row per candidate x case after a clean check. Run it when the benchmark runs, and commit the ledger.

`check` fails when:

- the round isn't frozen, or a candidate has no lock row;
- a locked field changed: the repo, a SHA, `locked_at` or the cases;
- a locked candidate was removed from the file;
- a run in this round saw another commit, or a case outside the round;
- a case in this round was already exposed to the candidate in another round. A candidate is the same if its name or its repo and head SHA match, so neither a new push nor a rename gets around this;
- the ledger or the round file is malformed.

Repeat runs inside one round are allowed.

## What this doesn't do

- **`check` is offline.** It compares the round file with the ledger. It doesn't ask GitHub whether a SHA exists or whether a branch has moved.
- **Append-only is a convention.** Only git history protects the ledger, and anyone with write access can rewrite both files.
- **Nothing calls it for you.** `lgcov bench` and `lgcov ci` don't run `lgcov round check`, so a round holds only where someone runs the command, and exposure is recorded only with `--record`.
- **A case is its id and source.** The ledger doesn't hash a case's content, so editing the question at `source` isn't noticed.
- **A retroactive lock proves nothing about the runs before it.** The T101 round was locked on 2026-11-07, after its runs on 2026-11-05, and says so. It records what those runs saw. Those runs had no run ids, so the ids are descriptive and the dates have no time.
