# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link]

**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: [e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]

**Primary Dependencies**: [e.g., FastAPI, UIKit, LLVM or NEEDS CLARIFICATION]

**Storage**: [if applicable, e.g., PostgreSQL, CoreData, files or N/A]

**Testing**: [e.g., pytest, XCTest, cargo test or NEEDS CLARIFICATION]

**Core Business Logic**: [identify business rules that require automated tests and their test levels]

**Comment Impact**: [new/updated zh-CN comments and required timestamp/version trace, or N/A]

**Sensitive Credentials**: [N/A, or identify credential types, injection, redaction, rotation, and leak checks; never include real values]

**Git Milestones**: [major-change boundaries that require validated, atomic commits]

**Documentation Impact**: [affected README/quickstart/runbook/config/contract/spec paths, required updates and validation; or N/A with reason]

**Target Platform**: [e.g., Linux server, iOS 15+, WASM or NEEDS CLARIFICATION]

**Project Type**: [e.g., library/cli/web-service/mobile-app/compiler/desktop-app or NEEDS CLARIFICATION]

**Performance Goals**: [domain-specific, e.g., 1000 req/s, 10k lines/sec, 60 fps or NEEDS CLARIFICATION]

**Constraints**: [domain-specific, e.g., <200ms p95, <100MB memory, offline-capable or NEEDS CLARIFICATION]

**Scale/Scope**: [domain-specific, e.g., 10k users, 1M LOC, 50 screens or NEEDS CLARIFICATION]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Required Evidence | Status |
|------|-------------------|--------|
| I. Clarity, extensibility, maintainability | Responsibilities, change boundaries, and extension points are explicit | [PASS/FAIL with evidence] |
| II. Decoupling and readability | Module contracts and dependency direction are explicit; no unjustified cycles or shared mutable state | [PASS/FAIL with evidence] |
| III. Simplest understandable implementation | The smallest viable approach is selected; added complexity is justified below | [PASS/FAIL with evidence] |
| IV. zh-CN traceable comments | Comment impact is identified; additions/updates use a date or version marker | [PASS/FAIL with evidence] |
| V. Git discipline | Major-change milestones and atomic commit boundaries are identified | [PASS/FAIL with evidence] |
| VI. Core business tests | Every core business rule maps to automated success, boundary, and critical-failure tests | [PASS/FAIL with evidence] |
| VII. Credential protection | Credential flow is N/A or protected through injection, least privilege, redaction, and leak prevention | [PASS/FAIL with evidence] |
| VIII. Major-update documentation sync | Every major update identifies affected project docs, required content, validation, and same-commit boundary; or records N/A with reason | [PASS/FAIL with evidence] |

Any FAIL blocks implementation. Complexity exceptions MUST be documented in Complexity
Tracking; principles IV–VIII cannot be waived by a feature-level exception.

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document the selected structure and reference the real
directories captured above]

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
