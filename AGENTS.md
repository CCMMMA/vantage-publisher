# AGENTS.md

## Scope
These instructions apply to the full repository.

## Goal
Keep the publisher stable for production station deployments while preserving compatibility with the existing `config.json` and `parameters.json` schemas.

## Environment
- Python: use `python3`.
- Main runtime script: `vantage-publisher-threading.py`.

## Workflow
1. Reproduce or describe the operational issue first.
2. Prefer minimal, reversible changes to data flow and config handling.
3. Validate syntax for edited Python files with `python3 -m py_compile <file>`.
4. Update `README.md` whenever behavior, payload format, or configuration keys change.

## Compatibility rules
- Do not rename or remove existing keys from `config.json` or `parameters.json`.
- New config keys must be optional with safe defaults.
- Preserve MQTT topic behavior (`topic == config.uuid`) unless explicitly requested.
- Preserve CSV path semantics unless a change is explicitly requested.

## Code style
- Keep logging explicit and operationally useful.
- Avoid broad refactors unrelated to the requested change.
- Add comments only where behavior is non-obvious.
