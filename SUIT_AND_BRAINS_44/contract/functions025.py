"""Bounded advisory transformations for the Build 025 pathways."""
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
import re
import unicodedata

from realcog.procedural_memory import canonical, identity

TEXT_RELATION = re.compile(r"(?:the )?([\w-]+) (bit|bites|contains|holds|is) (not )?(?:the )?([\w-]+)[.]?", re.I)


def label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("expected a nonempty label of at most 128 characters")
    return unicodedata.normalize("NFC", value.strip()).casefold()


def scalar(value):
    if type(value) not in (str, int, bool) or (isinstance(value, str) and len(value) > 128):
        raise ValueError("expected a bounded scalar")
    return value


def instant(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("event timestamp must include a time and timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("event timestamp must include a timezone")
    return result.astimezone(timezone.utc).isoformat()


def parse(observations):
    parsed, rejected = [], []
    for item in observations:
        source_id = identity(item)
        try:
            if not isinstance(item, dict):
                raise ValueError("observation envelope required")
            label(item.get("source"))
            if "text" in item:
                text = item["text"]
                if not isinstance(text, str) or len(text) > 256:
                    raise ValueError("bounded relation text required")
                quantifier = re.fullmatch(r"(not )?all ([\w-]+) ([\w-]+)[.]?", text, re.I)
                relation = TEXT_RELATION.fullmatch(text)
                if quantifier:
                    negation, subject, predicate = quantifier.groups()
                    fields = (subject, predicate, "all", bool(negation))
                elif relation:
                    subject, predicate, negation, value = relation.groups()
                    fields = (subject, predicate, value, bool(negation))
                else:
                    raise ValueError("ambiguous or unsupported relation text")
            else:
                fields = (item.get("subject"), item.get("predicate"), item.get("value"), item.get("negated", False))
            subject, predicate, value, negated = fields
            scalar(value)
            if type(negated) is not bool or type(item.get("uncertain", False)) is not bool:
                raise ValueError("negation and uncertainty must be boolean")
            parsed.append({"subject": subject, "predicate": predicate, "value": value, "negated": negated,
                           "scope": label(item.get("scope", "default")), "event": item.get("event"),
                           "arrival": item.get("arrival"), "source": item["source"], "digest": source_id,
                           "uncertain": item.get("uncertain", False)})
        except ValueError as exc:
            rejected.append({"digest": source_id, "reason": str(exc)})
    return {"accepted": parsed, "rejected": rejected}


def entities(rows):
    index, rejected = {}, []
    for row in rows:
        try:
            name = label(row["subject"])
            predicate = label(row["predicate"])
            value = label(row["value"]) if isinstance(row["value"], str) else row["value"]
        except ValueError as exc:
            rejected.append({"digest": row["digest"], "reason": str(exc)})
            continue
        entry = index.setdefault(name, {"spellings": [], "sources": []})
        if row["subject"] not in entry["spellings"]:
            entry["spellings"].append(row["subject"])
        entry["sources"].append(row["digest"])
        entry.setdefault("facts", []).append(dict(row, subject=name, predicate=predicate, value=value))
    return {"index": index, "rejected": rejected}


def relations(index):
    aliases = {"bit": "bite", "bites": "bite", "holds": "contains", "is": "state"}
    return [dict(row, predicate=aliases.get(row["predicate"], row["predicate"]))
            for entry in index.values() for row in entry["facts"]]


def compare(rows):
    def proposition(row):
        return (row["subject"], row["predicate"], row["scope"], row["event"])
    return [{"left": a["digest"], "right": b["digest"], "same_scope": proposition(a) == proposition(b),
             "same_value": canonical(a["value"]) == canonical(b["value"]),
             "same_polarity": a["negated"] == b["negated"], "same_source": a["source"] == b["source"],
             "certain": not (a["uncertain"] or b["uncertain"]),
             "exclusive": a["predicate"] == b["predicate"] == "state",
             "both_positive": not (a["negated"] or b["negated"])}
            for a, b in combinations(rows, 2)]


def temporal(rows):
    groups, missing = defaultdict(list), []
    for row in rows:
        try:
            groups[instant(row["event"])].append(row)
        except ValueError:
            missing.append(row["digest"])
    return {"groups": [{"instant": key, "rows": groups[key]} for key in sorted(groups)], "undated": missing}


def conflicts(pairs):
    return [row for row in pairs if row["certain"] and row["same_scope"] and
            ((row["same_value"] and not row["same_polarity"]) or
             (row["exclusive"] and row["both_positive"] and not row["same_value"]))]


def sufficiency(rows, contradictions, blocked):
    if contradictions:
        return {"status": "CONFLICT", "support": contradictions}
    if blocked or not rows or any(row["uncertain"] for row in rows):
        return {"status": "ABSTAIN", "support": [], "reason": "unresolved evidence"}
    values = {canonical([row["value"], row["negated"]]): [row["value"], row["negated"]] for row in rows}
    if len(values) != 1:
        return {"status": "ABSTAIN", "support": [], "reason": "multiple candidate conclusions"}
    value, negated = next(iter(values.values()))
    return {"status": "SUPPORTED", "answer": value, "negated": negated,
            "support": [row["digest"] for row in rows]}


def infer(premises, rules):
    """Forward chaining over explicit positive and negative literals."""
    facts = set(premises)
    for _ in range(len(rules) + 1):
        derived = {rule["then"] for rule in rules if set(rule["if"]) <= facts}
        if derived <= facts:
            break
        facts.update(derived)
    contradiction = any(fact[1:] in facts for fact in facts if fact.startswith("!"))
    return {"facts": sorted(facts), "contradiction": contradiction}


def hypotheses(templates, facts):
    return {key: template["predicts"] for key, template in templates.items()
            if set(template.get("requires", [])) <= set(facts)}


def gaps(candidates, observed):
    partitions = {}
    if candidates:
        common = set.intersection(*(set(predictions) for predictions in candidates.values()))
        for feature in sorted(common - observed.keys()):
            counts = defaultdict(int)
            for predictions in candidates.values():
                counts[canonical(predictions[feature])] += 1
            if len(counts) > 1:
                partitions[feature] = sorted(counts.values())
    return partitions


def select(partitions, preference):
    if not partitions:
        return {"feature": None, "used_method": False}
    best = next((key for key, profile in partitions.items() if profile == preference), None)
    return {"feature": best if best is not None else min(partitions), "used_method": best is not None}


def evaluate_cause(candidates, observed):
    remaining = {key: predictions for key, predictions in candidates.items()
                 if all(feature not in predictions or canonical(predictions[feature]) == canonical(value)
                        for feature, value in observed.items())}
    return {"remaining": remaining, "basis": "prediction_consistency",
            "causation": "UNDETERMINED"}


def learn(history):
    improvements = [entry for entry in history if entry["after"] < entry["before"]]
    if not improvements:
        return None
    best = max(improvements, key=lambda entry: (entry["before"] - max(entry["profile"]),
                                                 entry["before"], entry["before"] - entry["after"]))
    return best["profile"]


def transfer(records, partitions):
    available = list(partitions.values())
    for record in records:
        if record["profile"] in available:
            return record
    return None


def request_clarification(rejections):
    """Turn rejection metadata into a deduplicated structured evidence request."""
    required = set()
    for result in rejections:
        reason = result["reason"]
        required.update(("subject", "predicate", "value") if "relation" in reason or "label" in reason
                        else ("source", "subject", "predicate", "value", "negated", "uncertain"))
    return {"request": "clarify_observation", "required_fields": sorted(required),
            "observation_ids": sorted({record["digest"] for record in rejections})}


def synthesize(decision, relevant):
    """Group supporting or conflicting references by their presented source."""
    if decision["status"] == "SUPPORTED":
        references = set(decision["support"])
    elif decision["status"] == "CONFLICT":
        references = {pair[key] for pair in decision["support"] for key in ("left", "right")}
    else:
        references = set()
    groups = defaultdict(set)
    for row in relevant:
        if row["digest"] in references:
            groups[row["source"]].add(row["digest"])
    return {**decision, "presented_sources": {source: sorted(values) for source, values in sorted(groups.items())}}


OPERATIONS = {
    "L03": parse, "L04": entities, "L05": request_clarification, "L16": relations, "L17": compare,
    "L22": temporal, "L18": conflicts, "L31": sufficiency,
    "U06": infer, "U07": hypotheses, "U11": gaps, "U12": select,
    "U13": evaluate_cause, "U14": learn, "U15": transfer, "U16": synthesize,
}
