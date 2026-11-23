"""muse024 operation adapter for the Suit: scoped, audited, SQL-accounted."""
from contract import agent025
from suit61 import audit027, shadow029, store

SCOPES = {
    "suit_observe": {"suit:observe"},
    "suit_learn": {"suit:write"}, "suit_practice": {"suit:write"}, "suit_attempt": {"suit:write"},
    "suit_qualify_skill": {"suit:qualify"}, "suit_qualify_transfer": {"suit:qualify"}, "suit_revoke": {"suit:qualify"},
    "suit_retrieve": {"suit:execute"}, "suit_propose": {"suit:execute"}, "suit_begin": {"suit:execute", "suit:admit"},
    "suit_next": {"suit:execute"}, "suit_ack": {"suit:execute"}, "suit_resume": {"suit:execute"},
    "suit_shadow029": {"suit:read"},
    "suit_audit027": {"suit:read"},
    "suit_show": {"suit:read"},
}


def handle(db, principal, task, fault=None):
    store.setup(db)
    action = task["operation"]
    config = agent025.current(db, principal) if agent025.managed(db, principal) else None
    if config is not None and not config["suit_enabled"]:
        # An OFF switch removes optional procedural assistance only; authentication, scope checks and the
        # audit trail above this call are unchanged.
        return {"status": "SUIT_DISABLED", "agent_configuration_version": config["version"], "authority": "AUTH:0"}
    counts = {"reads": 0, "writes": 0}

    def count(sql):
        s = sql.lstrip().upper()
        if "SUIT61_" in s:
            if s.startswith("SELECT"):
                counts["reads"] += 1
            elif s.startswith(("INSERT", "UPDATE")):
                counts["writes"] += 1
    db.set_trace_callback(count)
    try:
        if action == "suit_ack":
            result = store.ack(db, principal, task, fault)
        elif action == "suit_shadow029":
            result = shadow029.receipts(db, principal, task)
        elif action == "suit_audit027":
            result = audit027.receipts(db, principal, task)
        else:
            result = store.HANDLERS[action](db, principal, task)
    finally:
        db.set_trace_callback(None)
    result = dict(result)
    result["suit_sql"] = counts
    if config is not None:
        result["agent_configuration_version"] = config["version"]
    return result
