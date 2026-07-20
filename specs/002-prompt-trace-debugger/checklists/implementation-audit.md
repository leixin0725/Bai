# 实现闭环审计：提示追踪调试器

**Purpose**: 在最终提交前逐项证明 spec、任务、测试、实现与文档的名义覆盖。
**Audited**: 2026-07-20
**Scope**: FR-001—FR-034、BR-001—BR-010、CR-001—CR-004、DR-001—DR-004、SC-001—SC-017。

## Functional Requirements

| Requirement | Primary task/test evidence | Status |
|---|---|:---:|
| FR-001 | T075/T080 · `test_cli_prompt_debug.py` | ✓ |
| FR-002 | T052/T088 · multi-call/scale coverage | ✓ |
| FR-003 | T027/T041 · provider materialization contract | ✓ |
| FR-004 | T027/T038 · provider payload comparison | ✓ |
| FR-005 | T053/T057 · call identity presentation | ✓ |
| FR-006 | T028/T044 · request parts/provenance | ✓ |
| FR-007 | T028/T039 · config/data file SourceRef | ✓ |
| FR-008 | T029/T039 · aggregated provenance | ✓ |
| FR-009 | T029/T045 · runtime/generated provenance | ✓ |
| FR-010 | T030/T054 · participation states | ✓ |
| FR-011 | T034/T043/T071 · mounted-ready/latency | ✓ |
| FR-012 | T051/T052/T056 · strict multi-call ordering | ✓ |
| FR-013 | T053/T054 · stable palette plus boundaries | ✓ |
| FR-014 | T053/T055/T075 · interactive no-color vs non-TTY | ✓ |
| FR-015 | T061/T062/T067 · context conservation/presentation | ✓ |
| FR-016 | T061/T067 · unknown/unavailable estimate | ✓ |
| FR-017 | T064/T068 · numeric actual-usage output | ✓ |
| FR-018 | T061/T067 · graded risk/main contribution | ✓ |
| FR-019 | T032/T041/T078 · presenter/sender release | ✓ |
| FR-020 | T076 · debug on/off equivalence | ✓ |
| FR-021 | T027/T076 · frozen request/read-only approval | ✓ |
| FR-022 | T031/T034/T076 · integrity/presentation fail closed | ✓ |
| FR-023 | T043/T084 · per-run private-memory warning | ✓ |
| FR-024 | T036/T038/T088 · adapter/persona/tool/retry coverage | ✓ |
| FR-025 | T003/T004/T069 · debug config/capability validation | ✓ |
| FR-026 | T034/T041 · call+attempt+digest approval binding | ✓ |
| FR-027 | T075/T080 · pre-persistence TTY/Textual probe | ✓ |
| FR-028 | T033/T046 · explicit reject rollback | ✓ |
| FR-029 | T008/T033/T077 · checkpoint/journal cleanup | ✓ |
| FR-030 | T033/T052 · discard approved auxiliary results | ✓ |
| FR-031 | T008/T079 · restart recovery blocks new work | ✓ |
| FR-032 | T009/T010/T079 · READY_PENDING/resume | ✓ |
| FR-033 | T012/T024 · read-only/write recovery gate | ✓ |
| FR-034 | T065/T066/T072 · V4 Flash migration/capabilities | ✓ |

## Business, Credential, and Documentation Requirements

| Requirement | Primary evidence | Status |
|---|---|:---:|
| BR-001 | T027/T041 · exact materialized provider request | ✓ |
| BR-002 | T028—T031/T039 · complete provenance | ✓ |
| BR-003 | T051/T052/T088 · approval/outbound bijection | ✓ |
| BR-004 | T061—T064/T067/T068 · honest estimate/actual usage | ✓ |
| BR-005 | T053—T055 · color-independent semantics | ✓ |
| BR-006 | T076 · debug equivalence/failure behavior | ✓ |
| BR-007 | T011/T032/T078/T089 · zero persistence/credential/references | ✓ |
| BR-008 | T008/T033/T077/T079 · rejectable transaction | ✓ |
| BR-009 | T009/T010/T079 · ordinary failure pending | ✓ |
| BR-010 | T012/T024 · tool transaction/compensation gate | ✓ |
| CR-001 | T011/T013/T083/T089 · prompt/trace/log/journal secret barriers | ✓ |
| CR-002 | T027/T040 · transport auth separated from materialized payload | ✓ |
| CR-003 | T013/T041/T083 · prompt guard plus safe incident state | ✓ |
| CR-004 | T089 · tracked/diff/history/runtime secret scan | ✓ |
| DR-001 | T048/T058/T070/T084 · README lifecycle guide | ✓ |
| DR-002 | T049/T059/T073/T085/T086 · executable quickstarts | ✓ |
| DR-003 | T025/T069/T072/T073 · configuration/model documentation | ✓ |
| DR-004 | T049/T073/T084—T086 · troubleshooting matrix | ✓ |

## Success Criteria

| Criterion | Measured evidence | Status |
|---|---|:---:|
| SC-001 | T088 · 200 mixed logical calls; approvals exactly equal physical outbound | ✓ |
| SC-002 | T027 · field-for-field fake SDK/HTTP payload comparison | ✓ |
| SC-003 | T028—T031 · included sources complete; unknown included source blocked | ✓ |
| SC-004 | T090 · 9/10 first-use trials locate source within 30s | ✓ |
| SC-005 | T063 · 40-case offline V4 Flash usage fixture, ≥95% tolerance | ✓ |
| SC-006 | T061/T062/T064 · conservation plus zero fabricated unknown usage | ✓ |
| SC-007 | T071 · Ubuntu 24.04/Python 3.13/80×24, 30 warm runs, p95≤500ms | ✓ |
| SC-008 | T053/T055/T075 · color/no-color semantics and non-TTY zero leak | ✓ |
| SC-009 | T076 · request/memory/tool difference count zero | ✓ |
| SC-010 | T078/T089 · usable secrets/persistent prompt traces/reference residue zero | ✓ |
| SC-011 | T090 · documented acceptance commands pass | ✓ |
| SC-012 | T078 · 1,000 approvals, presenter/sender residue zero | ✓ |
| SC-013 | T033 · reject at chat/curation/tool/retry checkpoints | ✓ |
| SC-014 | T008 · transaction fault-injection recovery | ✓ |
| SC-015 | T009/T010 · ordinary failure one pending; reject zero pending | ✓ |
| SC-016 | T012/T024 · current tools read-only; unsafe writes rejected before effect | ✓ |
| SC-017 | T065/T066/T072 · both profiles V4 Flash/non-thinking/8192/1M/384K | ✓ |

## Conflict Audit

- [x] non-TTY/redirection always fails before application build, persistence, or model send; it is not no-color degradation.
- [x] transaction vocabulary is exactly PREPARED, READY_PENDING, READY_TO_COMMIT.
- [x] approve closes/clears TUI before send; sender releases in `finally`; actual usage stays numeric and does not reopen TUI.
- [x] `deepseek-chat` appears only as migration history; active profiles use `deepseek-v4-flash`.
- [x] all 69 named FR/BR/CR/DR/SC identifiers have evidence above: nominal coverage 69/69 (100%).
