"""JSON schemas for Ollama's ``format`` parameter.

Pydantic emits ``$defs`` and ``$ref``. Ollama turns the schema into a grammar,
so the schema is inlined and trimmed to keep it simple and small.

Found against a real Ollama: a ``pattern`` (regex) keyword makes the grammar
builder fail with "failed to parse grammar". Pydantic still enforces patterns
when the reply is validated, so they are only removed from the wire schema.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Annotations the model does not need, plus keywords Ollama's grammar cannot parse.
_DROP = {"title", "default", "description", "pattern"}


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_resolve(n, defs) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        return _resolve(defs[name], defs)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            # Property NAMES are data: a field called "title" must survive.
            out[key] = {name: _resolve(sub, defs) for name, sub in value.items()}
        elif key in _DROP:
            continue
        else:
            out[key] = _resolve(value, defs)
    return out


def _require_all(node: Any) -> None:
    """Models should emit every field, so mark all properties required."""
    if isinstance(node, list):
        for n in node:
            _require_all(n)
    elif isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            node["required"] = list(props)
            for sub in props.values():
                _require_all(sub)
        for key in ("items", "anyOf"):
            if key in node:
                _require_all(node[key])


def inline_schema(model: type[BaseModel], *, require_all: bool = True) -> dict[str, Any]:
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})
    out = _resolve({k: v for k, v in raw.items() if k != "$defs"}, defs)
    if require_all:
        _require_all(out)
    return out
