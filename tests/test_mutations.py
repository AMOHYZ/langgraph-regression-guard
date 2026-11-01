"""T118: docs/mutations.yaml, the benchmark's mutation operators, checked against its schema
and against the docs it names: the taxonomy's KPIs and the #163 row's node names."""

import copy
import re
from pathlib import Path

import pytest
import yaml

from lgcov.attribution import parse_benchmark
from lgcov.taxonomy import load_taxonomy

DOCS = Path(__file__).resolve().parents[1] / "docs"
MUTATIONS = DOCS / "mutations.yaml"
ROW_163 = DOCS / "benchmark" / "open_deep_research-163.yaml"
OPERATORS = (
    "prompt_edit",
    "tool_schema_change",
    "router_flip",
    "state_key_rename",
    "model_swap",
    "retrieval_k_change",
    "send_fan_out_drop",
    "subgraph_output_change",
)
KINDS = ("node", "prompt", "tool", "router", "state_key")
CONFIDENCE = ("high", "medium", "low")
_KEYS = {"id", "operator", "confidence", "description", "location", "activation", "regression"}

TAXONOMY = load_taxonomy(DOCS / "taxonomy.yaml")
ROW = parse_benchmark(ROW_163.stem, yaml.safe_load(ROW_163.read_text("utf-8")), ROW_163.parent)
KPIS = {spec.name for spec in TAXONOMY.kpi_specs}
NODES = set(ROW.root | ROW.propagated) | {n for c in ROW.changed_nodes for n in c.nodes}


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _names(value: object) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    return None


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _entry(i: int, e: object, kpis: set[str], nodes: set[str], seen: set[str]) -> list[str]:
    if not isinstance(e, dict):
        return [f"operators[{i}] must be a mapping"]
    at = f"`{e['id']}`" if isinstance(e.get("id"), str) else f"operators[{i}]"
    found = [f"{at}: unknown keys {sorted(set(e) - _KEYS)}"] if set(e) - _KEYS else []
    if not isinstance(e.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9_]*", e["id"]):
        found.append(f"{at}: 'id' must be snake_case")
    elif e["id"] in seen:
        found.append(f"{at}: duplicate id")
    seen.add(str(e.get("id")))
    if e.get("operator") not in OPERATORS:
        found.append(f"{at}: 'operator' must be one of {list(OPERATORS)}")
    if e.get("confidence") not in CONFIDENCE:
        found.append(f"{at}: 'confidence' must be high, medium or low")
    found += [
        f"{at}: '{k}' must be a non-empty string"
        for k in ("description", "activation")
        if not _text(e.get(k))
    ]
    loc = _mapping(e.get("location"))
    if loc.get("kind") not in KINDS:
        found.append(f"{at}: 'location.kind' must be one of {list(KINDS)}")
    if not _text(loc.get("name")) or not str(loc.get("file", "")).endswith(".py"):
        found.append(f"{at}: 'location' needs a 'name' and a .py 'file'")
    reg = _mapping(e.get("regression"))
    if reg.get("kpi") not in kpis:
        found.append(f"{at}: 'regression.kpi' {reg.get('kpi')!r} isn't a KPI in docs/taxonomy.yaml")
    root, propagated = _names(reg.get("root")), _names(reg.get("propagated"))
    if not root:
        found.append(f"{at}: 'regression.root' must be a non-empty list of node names")
    if propagated is None:
        found.append(f"{at}: 'regression.propagated' must be a list of node names")
    both = sorted(set(root or ()) & set(propagated or ()))
    if both:
        found.append(f"{at}: {both} can't be both root and propagated")
    unknown = sorted({*(root or ()), *(propagated or ())} - nodes)
    if unknown:
        found.append(f"{at}: unknown nodes {unknown}")
    return found


def problems(doc: object, kpis: set[str], nodes: set[str]) -> list[str]:
    """Every way doc breaks the mutation-list schema, one message each; [] when it's valid."""
    if not isinstance(doc, dict):
        return ["the file must be a mapping"]
    found = [] if doc.get("version") == 1 else ["'version' must be 1"]
    found += [
        f"'{k}' must be a non-empty string"
        for k in ("system", "graph", "ref", "taxonomy")
        if not _text(doc.get(k))
    ]
    entries = doc.get("operators")
    if not isinstance(entries, list) or not entries:
        return [*found, "'operators' must be a non-empty list"]
    seen: set[str] = set()
    for i, e in enumerate(entries):
        found += _entry(i, e, kpis, nodes, seen)
    return found


def test_the_mutation_list_is_valid_against_its_schema_and_the_docs_it_names() -> None:
    doc = yaml.safe_load(MUTATIONS.read_text("utf-8"))
    assert problems(doc, KPIS, NODES) == []
    assert (doc["system"], doc["taxonomy"]) == (TAXONOMY.system, "docs/taxonomy.yaml")
    assert (doc["graph"], doc["ref"]) == (ROW.graph, ROW.head_ref)  # where it was all read


def test_it_covers_every_operator_class_in_8_to_10_entries() -> None:
    operators = [e["operator"] for e in yaml.safe_load(MUTATIONS.read_text("utf-8"))["operators"]]
    assert set(operators) == set(OPERATORS)
    assert 8 <= len(operators) <= 10


VALID: dict[str, object] = {
    "id": "an_edit",
    "operator": "prompt_edit",
    "confidence": "high",
    "description": "The prompt loses a sentence.",
    "location": {"kind": "prompt", "name": "lead_researcher_prompt", "file": "src/p.py"},
    "activation": "Any question.",
    "regression": {
        "kpi": "supervisor_delegates",
        "root": ["write_research_brief"],
        "propagated": ["final_report_generation"],
    },
}
_GONE = object()  # in a variant: the key is left out


def _doc(*entries: dict[str, object]) -> dict[str, object]:
    top = {"system": "s", "graph": "g.py:g", "ref": "a1", "taxonomy": "t.yaml"}
    return {"version": 1, **top, "operators": list(entries)}


def _broken(path: tuple[str, ...], value: object) -> dict[str, object]:
    entry = copy.deepcopy(VALID)
    target = entry
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    if value is _GONE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return entry


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("operator",), "rename_everything", "'operator' must be one of"),
        (("activation",), "", "'activation' must be a non-empty string"),
        (("confidence",), "certain", "'confidence' must be high, medium or low"),
        (("confidence",), _GONE, "'confidence' must be high, medium or low"),
        (("location", "kind"), "file", "'location.kind' must be one of"),
        (("regression", "kpi"), "nope", "'nope' isn't a KPI in docs/taxonomy.yaml"),
        (("regression", "root"), ["nowhere"], "unknown nodes ['nowhere']"),
        (("regression", "root"), [], "'regression.root' must be a non-empty list"),
        (("regression", "propagated"), ["write_research_brief"], "both root and propagated"),
        (("extra",), "x", "unknown keys ['extra']"),
    ],
)
def test_the_check_names_what_a_broken_entry_gets_wrong(
    path: tuple[str, ...], value: object, message: str
) -> None:
    found = problems(_doc(_broken(path, value)), KPIS, NODES)
    assert len(found) == 1 and found[0].startswith("`an_edit`: ") and message in found[0], found


def test_a_valid_entry_passes_and_two_cannot_share_an_id() -> None:
    assert problems(_doc(VALID), KPIS, NODES) == []
    assert problems(_doc(VALID, VALID), KPIS, NODES) == ["`an_edit`: duplicate id"]
