from __future__ import annotations

import sqlite3

from .config import Limits
from .identity import utc_now


def get_or_start(
    connection: sqlite3.Connection,
    project_id: str,
    agent: str,
    initial: int = Limits.run_budget,
) -> dict[str, int]:
    row = connection.execute(
        "SELECT remaining, initial FROM run_budgets WHERE project_id=? AND agent=?",
        (project_id, agent),
    ).fetchone()
    if row is not None:
        return {"remaining": int(row["remaining"]), "initial": int(row["initial"])}
    start = int(initial)
    connection.execute(
        "INSERT INTO run_budgets(project_id, agent, remaining, initial, updated_at) "
        "VALUES(?,?,?,?,?)",
        (project_id, agent, start, start, utc_now()),
    )
    return {"remaining": start, "initial": start}


def consume(
    connection: sqlite3.Connection,
    project_id: str,
    agent: str,
    n: int = 1,
) -> dict[str, int]:
    current = get_or_start(connection, project_id, agent)
    remaining = max(0, current["remaining"] - max(0, int(n)))
    connection.execute(
        "UPDATE run_budgets SET remaining=?, updated_at=? WHERE project_id=? AND agent=?",
        (remaining, utc_now(), project_id, agent),
    )
    return {"remaining": remaining, "initial": current["initial"]}


def set_budget(
    connection: sqlite3.Connection,
    project_id: str,
    agent: str,
    remaining: int,
) -> dict[str, int]:
    value = int(remaining)
    if value < 0:
        raise ValueError("remaining must be >= 0")
    row = connection.execute(
        "SELECT remaining, initial FROM run_budgets WHERE project_id=? AND agent=?",
        (project_id, agent),
    ).fetchone()
    now = utc_now()
    if row is None:
        initial = max(value, int(Limits.run_budget))
        connection.execute(
            "INSERT INTO run_budgets(project_id, agent, remaining, initial, updated_at) "
            "VALUES(?,?,?,?,?)",
            (project_id, agent, value, initial, now),
        )
        return {"remaining": value, "initial": initial}
    initial = int(row["initial"])
    if value > initial:
        initial = value
    connection.execute(
        "UPDATE run_budgets SET remaining=?, initial=?, updated_at=? WHERE project_id=? AND agent=?",
        (value, initial, now, project_id, agent),
    )
    return {"remaining": value, "initial": initial}
