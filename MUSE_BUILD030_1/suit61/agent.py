"""Four-mode orchestration: Suit ON/OFF x Mask ON/OFF, with a real Build 025 Lower/Higher port.

The Mask role here is deliberately narrow and measurable: when a target imposes ordering constraints,
the Higher/Lower temporal functions of Build 025 (executed through authenticated muse024.call, not mocks)
review whether a procedure's step order satisfies them. Mask output is advisory and can only be recorded
as evidence; qualification never depends on it (attempt logs are checked mechanically by the Suit store).
"""
import itertools
from muse024 import call
from suit61 import compare as C

REVIEW_LIMIT = 24


class BrainPort:
    def __init__(self, root, token):
        self.root, self.token = root, token
        self.invocations = 0        # authenticated cognitive pathway calls
        self.functions = 0          # L/U function steps executed inside those calls
        self.operation_cost = 0
        self.memory_reads = 0
        self.counter = itertools.count()

    def review_order(self, verbs, precedence, tag):
        """CONSISTENT / VIOLATED / NEED_INFORMATION for `precedence` pairs against the ordered `verbs`."""
        if not precedence:
            return {"result": "CONSISTENT", "calls": 0}
        if len(verbs) > REVIEW_LIMIT:
            return {"result": "NEED_INFORMATION", "calls": 0}
        names = [f"s{i:02d}_{v}" for i, v in enumerate(verbs)]
        first = {}
        for n, v in zip(names, verbs):
            first.setdefault(v, n)
        result, calls = "CONSISTENT", 0
        for a, b in precedence:
            if a not in first or b not in first:
                return {"result": "NEED_INFORMATION", "calls": calls}
            spec = {"domain": "temporal", "events": {n: None for n in names},
                    "before": [[names[i], names[i + 1]] for i in range(len(names) - 1)], "query": [first[a], first[b]]}
            out = call(self.root, self.token, {"operation": "evaluate", "task_id": f"{tag}-{next(self.counter)}", **spec})
            calls += 1
            self.invocations += 1
            self.functions += len(out.get("trace", []))
            self.operation_cost += out.get("operation_cost", 0)
            self.memory_reads += len(out.get("memory_accesses", []))
            status = out.get("status")
            if status == "AFTER":
                result = "VIOLATED"
            elif status != "BEFORE" and result != "VIOLATED":
                result = "NEED_INFORMATION"
        return {"result": result, "calls": calls}


class Agent:
    """suit/mask/transfer switches are independent. Mandatory authentication happens in muse024.call regardless."""
    def __init__(self, root, *, suit_token=None, brain_token=None, suit=True, mask=True, transfer=True, mode_label=None):
        self.root, self.suit_token, self.suit_on, self.mask_on, self.transfer = root, suit_token, suit, mask, transfer
        self.brain = BrainPort(root, brain_token) if mask and brain_token else None
        self.mode = mode_label or ("S%dM%d" % (suit, mask))
        self.sql = {"reads": 0, "writes": 0}

    def _suit(self, operation, **task):
        out = call(self.root, self.suit_token, {"operation": operation, **task})
        for k in ("reads", "writes"):
            self.sql[k] += out.get("suit_sql", {}).get(k, 0)
        return out

    def admit(self, task_id, target):
        """Review target constraints, then retrieve and admit through normal authenticated boundaries."""
        if not self.suit_on or not self.mask_on or self.brain is None:
            raise PermissionError("joint admission requires Suit and Mask")
        normalized = C.normalize_target(target)
        investigation = self.brain.review_order(normalized["required_verbs"], normalized["precedence"], task_id)
        got = self._suit("suit_retrieve", task_id=task_id, target=target, transfer_enabled=self.transfer)
        if not got["mode"].startswith("REUSE_"):
            return {"status": "HOLD", "investigation": investigation, "retrieval": got}
        review = self.brain.review_order([s["verb"] for s in got["plan"]["steps"]], normalized["precedence"], task_id)
        if review["result"] != "CONSISTENT":
            return {"status": "HOLD", "investigation": investigation, "review": review, "retrieval": got}
        result = self._suit("suit_begin", task_id=task_id, target=target, skill_id=got["skill_id"],
                            transfer_id=got.get("transfer_id"), retrieval_receipt=got["receipt"])
        return {"status": result["status"], "investigation": investigation, "review": review,
                "retrieval": got, "task": result}

    @staticmethod
    def _elements(target):
        c = target["context"]
        return len(set(target["required_verbs"])) + len(c["numeric"]) + len(c["categorical"]) + len(c["capabilities"]) + len(c["effects"])

    def handle(self, target, permitted=True):
        t = C.normalize_target(target)
        out = {"mode_config": self.mode, "evidence_requests": 0, "usable_plan": False}
        if not self.suit_on:
            if not self.mask_on:
                out.update(mode="BASELINE_NO_PROCEDURAL_NO_COGNITIVE", evidence_requests=self._elements(t))
                return out
            # Brains alone: no procedural memory to retrieve; they can only check the target's own ordering
            # and must request evidence for every element they would have to rediscover.
            review = self.brain.review_order(t["required_verbs"], t["precedence"], "A")
            out.update(mode="NEEDS_INVESTIGATION", evidence_requests=self._elements(t), brain_review=review["result"])
            return out
        got = self._suit("suit_retrieve", target=target, permitted=permitted, transfer_enabled=self.transfer)
        out.update(mode=got["mode"], familiarity=got["familiarity"], qualification=got["qualification"],
                   operational_permission=got["operational_permission"])
        if got["mode"].startswith("REUSE_"):
            out.update(usable_plan=True, plan=got["plan"], skill_id=got.get("skill_id"), transfer_id=got.get("transfer_id"))
            return out
        if got["mode"] == "PROPOSE_TRANSFER":
            review = {"result": None}
            if self.mask_on and self.brain and got.get("needs_review"):
                skill = self._suit("suit_show", kind="skill", item=got["skill_id"])["record"]
                order = [s["verb"] for s in skill["steps"]]
                review = self.brain.review_order(order, t["precedence"], "P")
                if review["result"] == "VIOLATED":
                    fixed = C.reorder(order, t["precedence"])
                    if fixed:
                        second = self.brain.review_order(fixed, t["precedence"], "P2")
                        review["second_review"] = second["result"]
            prop = self._suit("suit_propose", skill_id=got["skill_id"], target=target, retrieval_receipt=got["receipt"],
                              ordering_review={"result": review["result"], "invocation": {"calls": review.get("calls", 0)}})
            comp = prop["comparison"]
            out.update(transfer_id=prop["transfer_id"], proposal_disposition=prop["disposition"], skill_id=got["skill_id"],
                       brain_review=review["result"], state=prop["state"], usable_plan=False,
                       evidence_requests=comp["counts"][C.U])
            if prop["disposition"] == "DO_NOT_TRANSFER":
                out["mode"] = "DO_NOT_TRANSFER"
            return out
        if got["mode"] == "DO_NOT_TRANSFER":
            out["reason"] = got.get("reason")
            return out
        out["evidence_requests"] = self._elements(t)          # unfamiliar: must be investigated, not prohibited
        if self.mask_on and self.brain:
            out["brain_review"] = self.brain.review_order(t["required_verbs"], t["precedence"], "U")["result"]
        return out
