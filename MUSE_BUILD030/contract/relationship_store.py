"""Persistent relationship memory, deliberately separate from StrategyStore."""
from __future__ import annotations

import json
import os
from typing import Any, Iterable, Mapping

class RelationshipStoreError(ValueError):
    pass

class RelationshipStore:
    SCHEMA = "muse-relationship-store/1"

    def __init__(self, path: str | None = None):
        self.path = path
        self.records: list[dict[str, Any]] = []
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict) or data.get("schema") != self.SCHEMA:
                raise RelationshipStoreError("invalid relationship store schema")
            self.records = list(data.get("relationships", []))
            self._validate()

    def _validate(self):
        seen = set()
        for record in self.records:
            required = {"relationship_id", "structure", "evidence_ids", "provenance",
                        "qualification", "confidence", "applicability"}
            if not required.issubset(record):
                raise RelationshipStoreError("relationship record missing required fields")
            if record["relationship_id"] in seen:
                raise RelationshipStoreError("duplicate relationship_id")
            seen.add(record["relationship_id"])

    def save(self, relationship: Mapping[str, Any]) -> dict[str, Any]:
        record = dict(relationship)
        self.records = [r for r in self.records if r.get("relationship_id") != record.get("relationship_id")]
        self.records.append(record)
        self._validate()
        if self.path:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as stream:
                json.dump({"schema": self.SCHEMA, "relationships": self.records},
                          stream, sort_keys=True, indent=2)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(tmp, self.path)
        return record

    def save_many(self, relationships: Iterable[Mapping[str, Any]]) -> None:
        for relationship in relationships:
            self.save(relationship)

    def get(self, relationship_id: str) -> dict[str, Any] | None:
        for record in self.records:
            if record.get("relationship_id") == relationship_id:
                return dict(record)
        return None

    def retrieve(self, *, qualification: str = "VALID", input_keys: Iterable[str] = ()) -> list[dict[str, Any]]:
        keys = set(input_keys)
        matches = []
        for record in self.records:
            if qualification and record.get("qualification") != qualification:
                continue
            applicable = set(record.get("applicability", {}).get("input_keys", []))
            if keys and not keys.issubset(applicable):
                continue
            matches.append(dict(record))
        return sorted(matches, key=lambda r: (-float(r.get("confidence", 0)), r["relationship_id"]))
