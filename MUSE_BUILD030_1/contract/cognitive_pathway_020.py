"""Build 020: observable, restart-persistent, deterministic cognition path.

This is a bounded cognitive implementation, not a claim of 98 independent solvers.
The trace records real dispatcher calls; a solver's self-reported functions_invoked
is retained separately and is never treated as execution evidence.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from .cognitive_domains_023 import parse_math, solve_math
from .brains_wiring import BrainsWiringRegistry
from .observation_memory import record_observations, recall_observations
from .seven_domain_cognition import evaluate_seven_domain_task


def _solve_linear(text: str) -> dict[str, Any] | None:
    try:
        family, values = parse_math(text)
    except ValueError:
        if "=" in text:
            raise
        return None
    if family != "linear":
        return None
    result, _ = solve_math("linear_general", values)
    if result["status"] != "ANSWERED":
        return {"status": "JUSTIFIED_ABSTENTION", "reason":
                "INFINITELY_MANY_SOLUTIONS" if values[1] == values[2] else "NO_SOLUTION"}
    if "/" not in result["answer"]:
        result["answer"] = int(result["answer"])
    return {**result, "method": "ISOLATE_LINEAR_VARIABLE",
            "coefficients": dict(zip(("a", "b", "c"), map(str, values)))}


def _db(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    db=sqlite3.connect(root/'cognitive_020.sqlite3',timeout=10)
    try:
        db.execute('PRAGMA busy_timeout=10000')
        db.execute('CREATE TABLE IF NOT EXISTS methods (method_id TEXT PRIMARY KEY, domain TEXT NOT NULL, description TEXT NOT NULL, first_task TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS executions (digest TEXT PRIMARY KEY, task_id TEXT NOT NULL, result_json TEXT NOT NULL)')
    except BaseException:
        db.close()
        raise
    return db


def run_cognitive_pathway(root: Path, task: dict[str, Any]) -> dict[str, Any]:
    if type(task.get('authority',0)) is not int or task.get('authority',0)!=0:
        raise PermissionError('AUTH:0 required')
    if task.get('task_type')!='cognitive_020':
        raise ValueError('task_type must be cognitive_020')
    tid=task.get('task_id')
    if not isinstance(tid,str) or not tid.strip():
        raise ValueError('nonempty task_id required')
    domain=task.get('cognitive_domain')
    if domain not in {'mathematics','number','grammar','time','nimble','manic_stability','pictures'}:
        raise ValueError('unsupported cognitive_domain')
    observations=task.get('observations',[])
    if not isinstance(observations,list):
        raise ValueError('observations must be list')
    if observations:
        source=task.get('source_id')
        if not isinstance(source,str) or not source.strip():
            raise ValueError('source_id required with observations')
        # Validate the full batch before writing, via observation module.
        from .observation_memory import _validate
        for ob in observations: _validate(ob)
    else:
        source=task.get('source_id')
    trace=[]
    registry=BrainsWiringRegistry(trace_fn=lambda fid,result:trace.append({'function_id':fid,'result':result}))
    for fid in ('L01','U09'):
        registry.invoke(fid,{'authority':0,'task_id':tid})
    text = str(task.get("text") or "")
    linear = _solve_linear(text) if domain == "mathematics" else None
    db=_db(Path(root))
    try:
        method_id='linear_equation_v1'
        prior=db.execute('SELECT 1 FROM methods WHERE method_id=?',(method_id,)).fetchone() is not None
        reads=1
        if linear is not None:
            result={'domain':domain,**linear,'functions_invoked':['L01','U09','U37'] if linear['status']=='ANSWERED' else ['L01','U09','U10']}
            learned=linear['status']=='ANSWERED'
        else:
            result=evaluate_seven_domain_task(task,execution_id=tid)
            learned=False
        final_fid='U37' if result.get('status')=='ANSWERED' else 'U10'
        registry.invoke(final_fid,{'authority':0,'task_id':tid})
        if observations:
            record_observations(Path(root),{'task_type':'observe','source_id':source,'observations':observations})
        digest=hashlib.sha256(json.dumps({'task_id':tid,'task':task},sort_keys=True,default=str).encode()).hexdigest()
        writes=0
        with db:
            if learned and not prior:
                db.execute('INSERT OR IGNORE INTO methods VALUES (?,?,?,?)',(method_id,domain,'Solve ax+b=c by isolating x',tid))
                writes+=db.execute('SELECT changes()').fetchone()[0]
            db.execute('INSERT OR IGNORE INTO executions VALUES (?,?,?)',(digest,tid,json.dumps(result,sort_keys=True,default=str)))
            writes+=db.execute('SELECT changes()').fetchone()[0]
        return {'task_id':tid,'status':result.get('status'),'answer':result.get('answer'),
                'method_retrieved':method_id if prior and linear is not None else None,
                'method_learned':method_id if learned and not prior else None,
                'memory_reads':reads,'memory_writes':writes,
                'observations':recall_observations(Path(root),source)['observations'] if isinstance(source,str) and source.strip() else [],
                'actual_dispatcher_calls':trace,'solver_reported_functions':result.get('functions_invoked',[]),
                'solver_result':result}
    finally:
        db.close()
