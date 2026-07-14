"""
Episode harness: run one agent against a FRESH world and grade the trajectory.
Deterministic for scripted agents; stochastic for LLM agents (hence pass^k).
"""

from concurrent.futures import ThreadPoolExecutor
from world import World, fresh_metrics
from rubric import grade

MAX_WORKERS = 12   # episodes are network-bound; threads give ~linear speedup


def _is_api_error(e: Exception) -> bool:
    name = type(e).__name__.lower()
    blob = (name + " " + str(e)).lower()
    return any(s in blob for s in (
        "ratelimit", "rate limit", "quota", "authentication", "apierror",
        "apiconnection", "429", "insufficient", "overloaded", "timeout",
    ))


def run_episode(agent) -> dict:
    fresh_metrics()          # reset module-level metric state
    w = World()
    api_error = None
    try:
        agent(w)
    except Exception as e:
        if _is_api_error(e):
            api_error = str(e)[:120]     # infra failure, NOT a task failure
        else:
            # a genuinely crashing agent fails the task
            w.trajectory.append({"tool": "ERROR", "arg": str(e), "result": "", "kind": "write"})
    result = grade(w)
    result["trajectory"] = w.trajectory
    result["api_error"] = api_error
    if api_error:
        result["passed"] = False
    return result


def run_k(agent, k: int, parallel: bool = True) -> dict:
    """Run k independent episodes. API errors are excluded from scoring so quota
    hiccups never masquerade as model/task performance. Episodes run concurrently
    (thread pool) since each World is fully self-contained."""
    if parallel and k > 1:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, k)) as ex:
            runs = list(ex.map(lambda _: run_episode(agent), range(k)))
    else:
        runs = [run_episode(agent) for _ in range(k)]
    ok = [r for r in runs if not r["api_error"]]          # scoreable runs
    n = len(ok)
    errored = k - n
    if n == 0:
        return {"k": k, "errored": errored, "scoreable": 0, "pass@1": None,
                "pass^k": None, "outage_rate": None, "avg_tool_calls": None, "runs": runs}
    passes = sum(r["passed"] for r in ok)
    return {
        "k": k, "errored": errored, "scoreable": n,
        "pass@1": passes / n,
        "pass^k": 1.0 if passes == n else 0.0,   # succeeded on EVERY scoreable trial
        "outage_rate": sum(r["caused_outage"] for r in ok) / n,
        "avg_tool_calls": sum(r["tool_calls"] for r in ok) / n,
        "runs": runs,
    }
