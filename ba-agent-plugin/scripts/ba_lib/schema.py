"""Minimal JSON Schema (draft 2020-12 subset) validator, standard library only.

Supported keywords: type, enum, const, pattern, minLength, minimum, minItems, required,
properties, additionalProperties (bool), items, anyOf, $ref (to #/$defs/...).
The schema files are ordinary JSON Schema, so they can also be checked with the
`jsonschema` package where it is installed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
_cache: dict[str, dict] = {}

_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def load_schema(name: str) -> dict:
    if name not in _cache:
        with open(SCHEMA_DIR / f"{name}.schema.json", "r", encoding="utf-8") as f:
            _cache[name] = json.load(f)
    return _cache[name]


def _resolve(ref: str, root: dict) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref {ref}")
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check(value, schema: dict, root: dict, path: str, errors: list[str]) -> None:
    if "$ref" in schema:
        _check(value, _resolve(schema["$ref"], root), root, path, errors)
        return
    if "anyOf" in schema:
        for sub in schema["anyOf"]:
            trial: list[str] = []
            _check(value, sub, root, path, trial)
            if not trial:
                break
        else:
            errors.append(f"{path}: does not match any allowed form")
        return
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](value) for x in types):
            errors.append(f"{path}: expected {'|'.join(types)}, got {type(value).__name__}")
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value.strip()) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "items" in schema:
            for i, item in enumerate(value):
                _check(item, schema["items"], root, f"{path}[{i}]", errors)
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required field '{req}'")
        props = schema.get("properties", {})
        for k, sub in props.items():
            if k in value:
                _check(value[k], sub, root, f"{path}.{k}", errors)
        if schema.get("additionalProperties") is False:
            for k in value:
                if k not in props:
                    errors.append(f"{path}: unexpected field '{k}'")


def validate(instance, schema_name: str) -> list[str]:
    schema = load_schema(schema_name)
    errors: list[str] = []
    _check(instance, schema, schema, "$", errors)
    return errors
