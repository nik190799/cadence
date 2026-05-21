# Schemas

JSON Schemas for the structured data Cadence produces and consumes.

| File | Purpose |
|---|---|
| `cadence-yaml.schema.json` | Validates user-authored `.cadence/cadence.yaml` |
| `retro.schema.json` | Validates the JSON form of a retrospective entry |

## Status

Stub schemas in v0.0.1 (Phase 0). Full schemas with validation
constraints in Phase 1.

## Usage

CI (`.github/workflows/ci.yml`) validates that every shipped YAML/JSON
conforms to its schema. Users can validate their own
`.cadence/cadence.yaml` with any JSON Schema validator pointed at
`cadence-yaml.schema.json`.
