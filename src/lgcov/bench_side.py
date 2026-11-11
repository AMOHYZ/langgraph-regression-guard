"""T166: one side of `lgcov bench`, in a process of its own.

Base and head are two versions of the same code. In one process they would share one copy of
every module they import, since Python hands head whatever base loaded. So `lgcov bench` starts
each side here, with that side's folders first on its path, and a run crosses over as a file.

    check  <target>                                can the graph be loaded?
    record <target> <cases.json> <runs.json>       record each case, and save the runs
    replay <target> <runs.json> <drifted> <fresh>  replay them, and save the nodes that drifted in
           [<probes>] [--live]                     each, the fresh runs, and T162's probe of each
                                                   changed node that drifted; --live runs every node

T169: runs are saved as each one finishes, so a crash can't lose what was already paid for.

On failure the last line of stderr says why. Exit CANT_IMPORT: the graph couldn't be loaded, so
nothing ran. Exit 1: it failed later.
"""

import json
import sys
from pathlib import Path

from lgcov.cassette import (
    Cassette,
    Replay,
    _codes,
    probe_nodes,
    record,
    replay_run,
    runs_from_json,
    runs_to_json,
)
from lgcov.cli import CANT_IMPORT, _load_graph, _say_replay_notes


def _save(path: Path, runs: list[Cassette]) -> None:
    # All of it or none: a crash mid-write can't leave half a file where a paid run was.
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    partial.write_text(runs_to_json(runs), encoding="utf-8")
    partial.replace(path)


def _probes(graph: object, recorded: list[Cassette], drifted: list[list[str]]) -> object:
    # T162: each node whose code changed and that drifted, run on base's input, offline.
    codes = _codes(graph)
    changed = {
        s.node
        for r in recorded
        for s in r.steps
        if s.code is not None and codes.get(s.node) not in (None, s.code)
    }
    probes = probe_nodes(graph, recorded, changed & {n for d in drifted for n in d})
    return {n: [p.drifted, p.reason] for n, p in probes.items()}


def side_main(argv: list[str]) -> int:
    mode, target, *paths = argv
    live = paths[-1:] == ["--live"]  # T173: run every node, serve none
    try:
        # Exception: importing user graph code can raise anything.
        graph = _load_graph(target)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return CANT_IMPORT
    try:
        if mode == "record":
            runs: list[Cassette] = []
            for case in json.loads(Path(paths[0]).read_text(encoding="utf-8")):
                runs.append(record(graph, dict(case)))
                _save(Path(paths[1]), runs)
        elif mode == "replay":
            done: list[Replay] = []
            fresh: list[Cassette] = []
            recorded = runs_from_json(Path(paths[0]).read_text(encoding="utf-8"))
            try:
                for run in recorded:
                    done.append(replay_run(graph, run, (), serve=not live))
                    fresh.append(Cassette(run.inputs, list(done[-1].steps)))
                    _save(Path(paths[2]), fresh)
            finally:
                _say_replay_notes(done)
            drifted = [[d.node for d in r.drifts] for r in done]
            Path(paths[1]).write_text(json.dumps(drifted), encoding="utf-8")
            if len([p for p in paths if p != "--live"]) > 3:  # T162: the probes' file
                Path(paths[3]).write_text(json.dumps(_probes(graph, recorded, drifted)), "utf-8")
    except Exception as exc:  # user graph code can raise anything: the parent reports it
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(side_main(sys.argv[1:]))
