from __future__ import annotations

import sqlite3

from .identity import utc_now

# No row means no budget: queries are not counted down unless an agent or the
# user sets one explicitly.
UNBUDGETED: dict[str, int | None] = {"remaining": None, "initial": None}


def get_budget(
    connection: sqlite3.Connection,
    project_id: str,
    agent: str,
) -> dict[str, int | None]:
    row = connection.execute(
        "SELECT remaining, initial FROM run_budgets WHERE project_id=? AND agent=?",
        (project_id, agent),
    ).fetchone()
    if row is None:
        return dict(UNBUDGETED)
    return {"remaining": int(row["remaining"]), "initial": int(row["initial"])}


def consume(
    connection: sqlite3.Connection,
    project_id: str,
    agent: str,
    n: int = 1,
) -> dict[str, int | None]:
    current = get_budget(connection, project_id, agent)
    if current["remaining"] is None:
        return current
    remaining = max(0, int(current["remaining"]) - max(0, int(n)))
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
) -> dict[str, int | None]:
    value = int(remaining)
    if value < 0:
        raise ValueError("remaining must be >= 0")
    row = connection.execute(
        "SELECT remaining, initial FROM run_budgets WHERE project_id=? AND agent=?",
        (project_id, agent),
    ).fetchone()
    now = utc_now()
    if row is None:
        connection.execute(
            "INSERT INTO run_budgets(project_id, agent, remaining, initial, updated_at) "
            "VALUES(?,?,?,?,?)",
            (project_id, agent, value, value, now),
        )
        return {"remaining": value, "initial": value}
    initial = max(int(row["initial"]), value)
    connection.execute(
        "UPDATE run_budgets SET remaining=?, initial=?, updated_at=? WHERE project_id=? AND agent=?",
        (value, initial, now, project_id, agent),
    )
    return {"remaining": value, "initial": initial}


def clear_budget(
    connection: sqlite3.Connection,
    project_id: str,
    agent: str,
) -> dict[str, int | None]:
    connection.execute(
        "DELETE FROM run_budgets WHERE project_id=? AND agent=?",
        (project_id, agent),
    )
    return dict(UNBUDGETED)
