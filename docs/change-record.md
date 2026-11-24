# Change record export (T129)

`lgcov change-record export` writes **JSON and markdown** for one PR from a
change-record file (T155):

diff → affected nodes → KPIs → verdict → approver → state.

## Evidence mapping (not a compliance claim)

The markdown cites these as **evidence only** — never as a claim that the system
is certified or conforming:

| Reference | What the record shows as evidence |
|---|---|
| EU AI Act Art. 17(1)(a) | Change description (diff summary) |
| EU AI Act Art. 17(1)(d) | Quality control steps (KPIs + statistical verdict) |
| EU AI Act Art. 17(1)(k) | Post-change monitoring handoff (approver + adoption state) |
| ISO/IEC 42001 8.1 (provisional) | Operational planning/control fields for this change |

Do not treat an exported file as proof of legal conformity.
