"""Bounded parsers and allowlisted strategies used by the procedural runtime."""
from datetime import datetime, timezone
from fractions import Fraction
import re

NUMBER = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
LINEAR = re.compile(
    rf"(?P<sign>[+-]?)\s*(?:(?P<a>{NUMBER})\s*\*?\s*)?x"
    rf"\s*(?:(?P<op>[+-])\s*(?P<b>{NUMBER}))?\s*=\s*(?P<c>[+-]?{NUMBER})",
    re.I,
)
PAIR = re.compile(r"sum\s*=\s*([+-]?[0-9]+)\s*,\s*difference\s*=\s*([+-]?[0-9]+)", re.I)
RELATIONS = {
    "owns": ("owns", "possesses"),
    "visits": ("visits", "goes to"),
    "supports": ("supports", "backs"),
}
ALIASES = {alias: predicate for predicate, group in RELATIONS.items() for alias in group}
STATEMENT = re.compile(r"([A-Za-z][\w-]*) (not )?(.*?) ([A-Za-z][\w-]*)[.]?")
FAMILIES = {
    "linear": ("linear_general", "linear_unit_zero_offset"),
    "sum_difference": ("sum_difference",),
    "semantic": ("semantic_scan", "semantic_index"),
    "temporal": ("temporal_closure", "temporal_path"),
}


def parse_math(value):
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError("expected bounded equation text")
    text = value.strip()
    if match := LINEAR.fullmatch(text):
        groups = match.groupdict()
        a = Fraction(groups["a"] or "1") * (-1 if groups["sign"] == "-" else 1)
        b = Fraction(groups["b"] or "0") * (-1 if groups["op"] == "-" else 1)
        return "linear", (a, b, Fraction(groups["c"]))
    if match := PAIR.fullmatch(text):
        return "sum_difference", tuple(map(Fraction, match.groups()))
    raise ValueError("expected ax+b=c or sum=S,difference=D")


def statement(value):
    if isinstance(value, str):
        match = STATEMENT.fullmatch(" ".join(value.lower().split()))
        if not match:
            raise ValueError("invalid bounded statement")
        subject, negated, relation, obj = match.groups()
        positive = not negated
    elif isinstance(value, dict):
        subject, relation, obj = (value.get(key) for key in ("subject", "relation", "object"))
        positive = value.get("positive", True)
        if not all(isinstance(item, str) for item in (subject, relation, obj)) or type(positive) is not bool:
            raise ValueError("invalid statement fields")
        subject, relation, obj = (item.lower().strip() for item in (subject, relation, obj))
        if not all(re.fullmatch(r"[a-z][\w-]*", item) for item in (subject, obj)):
            raise ValueError("expected single-token entities")
    else:
        raise ValueError("expected statement text or object")
    if relation not in ALIASES:
        raise ValueError("unsupported relation")
    return subject, relation, obj, positive


def parse_temporal(task):
    times = task.get("events", {})
    before = task.get("before", [])
    query = task.get("query")
    if not isinstance(times, dict) or not isinstance(before, list) or not isinstance(query, list):
        raise ValueError("expected events object, before list and query pair")
    if len(times) > 32 or len(before) > 64 or len(query) != 2:
        raise ValueError("temporal input exceeds supported limits")
    def pair(value):
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(x, str) and x for x in value):
            raise ValueError("expected pair of nonempty event IDs")
        return tuple(value)
    utc = {}
    for name, text in times.items():
        if not isinstance(name, str) or not name:
            raise ValueError("invalid event ID")
        if text is None:
            continue
        if not isinstance(text, str):
            raise ValueError("expected ISO timestamp or null")
        stamp = datetime.fromisoformat(text)
        if stamp.tzinfo is None or stamp.microsecond:
            raise ValueError("timestamps require explicit offset and whole-second precision")
        utc[name] = stamp.astimezone(timezone.utc)
    edges = [pair(edge) for edge in before]
    query = pair(query)
    nodes = set(times) | set(query) | {n for edge in edges for n in edge}
    if len(nodes) > 32:
        raise ValueError("too many event IDs")
    return utc, edges, query, nodes


def parse(task):
    domain = task.get("domain")
    if domain == "mathematics":
        return parse_math(task.get("text"))
    if domain == "semantic":
        items = task.get("statements", [])
        if not isinstance(items, list) or len(items) > 64:
            raise ValueError("expected at most 64 statements")
        return "semantic", ([statement(x) for x in items], statement(task.get("query")))
    if domain == "temporal":
        return "temporal", parse_temporal(task)
    raise ValueError("unsupported domain")


def rational(value):
    return str(value)


def applicable(name, data):
    return name != "linear_unit_zero_offset" or (data[0] == 1 and data[1] == 0)


def solve_math(name, data):
    if name == "sum_difference":
        total, delta = data
        x = (total + delta) / 2
        return {"status": "ANSWERED", "answer": [rational(x), rational(total - x)]}, 3
    a, b, c = data
    if name == "linear_unit_zero_offset":
        return {"status": "ANSWERED", "answer": rational(c)}, 0
    if a == 0:
        return {"status": "INDETERMINATE" if b == c else "INCONSISTENT", "answer": None}, 1
    answer = (c - b) / a
    return {"status": "ANSWERED", "answer": rational(answer)}, 2


def solve_semantic(name, data):
    rows, query = data
    checked = 0
    def normalize(value):
        nonlocal checked
        if name == "semantic_index":
            checked += 1
            return ALIASES[value]
        for predicate, aliases in RELATIONS.items():
            for alias in aliases:
                checked += 1
                if value == alias:
                    return predicate
        raise ValueError("unknown relation")
    target = (query[0], normalize(query[1]), query[2])
    facts = {}
    for subject, relation, obj, positive in rows:
        key = subject, normalize(relation), obj
        facts.setdefault(key, set()).add(positive)
    observed = facts.get(target, set())
    status = ("CONTRADICTION" if len(observed) == 2 else "SUPPORTED" if query[3] in observed
              else "OPPOSED" if observed else "NEED_INFORMATION")
    return {"status": status, "answer": status, "query": list(target),
            "external_truth_evaluated": False,
            "information_request": list(target) if not observed else None}, checked


def solve_temporal(name, data):
    utc, edges, (left, right), nodes = data
    graph = {n: set() for n in nodes}
    ordered = sorted(utc, key=lambda n: (utc[n], n))
    for x, y in edges:
        graph[x].add(y)
    # Add a linear-size timestamp order via virtual representatives per instant.
    groups = []
    for node in ordered:
        if not groups or utc[groups[-1][0]] != utc[node]:
            groups.append([])
        groups[-1].append(node)
    for earlier, later in zip(groups, groups[1:]):
        for node in earlier:
            graph[node].update(later)
    cost = 0
    def reachable(start):
        nonlocal cost
        todo, seen = [start], set()
        while todo:
            node = todo.pop()
            for nxt in sorted(graph[node]):
                cost += 1
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return seen
    inconsistent = False
    if name == "temporal_closure":
        closure = {node: reachable(node) for node in sorted(nodes)}
        lookup = closure.__getitem__
        for start, seen in closure.items():
            if start in seen or (start in utc and any(n in utc and utc[n] <= utc[start] for n in seen)):
                inconsistent = True
    else:
        from heapq import heapify, heappop, heappush
        indegree = dict.fromkeys(nodes, 0)
        for outgoing in graph.values():
            for target in outgoing:
                indegree[target] += 1
                cost += 1
        ready = [node for node in nodes if indegree[node] == 0]
        heapify(ready)
        latest_ancestors = {}
        visited = 0
        while ready:
            node = heappop(ready)
            visited += 1
            ancestor = latest_ancestors.get(node)
            if node in utc:
                if ancestor is not None and ancestor >= utc[node]:
                    inconsistent = True
                ancestor = utc[node] if ancestor is None else max(ancestor, utc[node])
            for target in sorted(graph[node]):
                cost += 1
                if ancestor is not None and (target not in latest_ancestors or ancestor > latest_ancestors[target]):
                    latest_ancestors[target] = ancestor
                indegree[target] -= 1
                if indegree[target] == 0:
                    heappush(ready, target)
        inconsistent = inconsistent or visited != len(nodes)
        lookup = reachable
    interval = None
    if inconsistent:
        status = "CONTRADICTION"
    elif left == right:
        status = "SIMULTANEOUS"
        interval = 0
    elif left in utc and right in utc:
        interval = int((utc[right] - utc[left]).total_seconds())
        status = "BEFORE" if interval > 0 else "AFTER" if interval < 0 else "SIMULTANEOUS"
    else:
        status = "BEFORE" if right in lookup(left) else "AFTER" if left in lookup(right) else "NEED_INFORMATION"
    return {"status": status, "answer": status, "interval_seconds": interval,
            "timeline_utc": [[event, utc[event].isoformat()] for event in ordered],
            "information_request": [left, right] if status == "NEED_INFORMATION" else None}, cost


def execute(family, strategy, data):
    if strategy not in FAMILIES[family] or not applicable(strategy, data):
        raise ValueError("inapplicable strategy")
    if family == "semantic":
        return solve_semantic(strategy, data)
    if family == "temporal":
        return solve_temporal(strategy, data)
    return solve_math(strategy, data)
