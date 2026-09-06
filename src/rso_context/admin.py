from __future__ import annotations

import html
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from .db import Database
from .pointers import PlateCache, hydrate_chunk_row


PROJECT_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _bound_project_id(value: str | None) -> str | None:
    if not value or not PROJECT_ID_RE.fullmatch(value):
        return None
    return value


def build_admin_html(database: Database, project_id: str | None = None) -> str:
    """Read-only HTML for projects, sources, pointer chunks, claims, and validations."""
    database.initialize()
    bound = _bound_project_id(project_id)
    cache = PlateCache()
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>RSO Context admin</title>",
        "<style>",
        "body{font-family:sans-serif;margin:1.5rem;max-width:1100px}",
        "table{border-collapse:collapse;width:100%;margin:0.5rem 0 1.5rem}",
        "th,td{border:1px solid #ccc;padding:4px 8px;vertical-align:top;font-size:0.9rem}",
        "th{text-align:left;background:#f4f4f4}",
        "pre{white-space:pre-wrap;margin:0;font-size:0.85rem}",
        ".stale{color:#a40;font-weight:600}",
        "h1,h2,h3{margin-bottom:0.4rem}",
        "</style></head><body>",
        "<h1>RSO Context admin</h1>",
        "<p>Read-only viewer. Chunks are pointers; plate text is reconstructed from the live file.</p>",
    ]
    with database.connect() as connection:
        if bound:
            project_rows = connection.execute(
                "SELECT id, display_name, scope, kind, corpus_version FROM projects WHERE id=?",
                (bound,),
            ).fetchall()
        else:
            project_rows = connection.execute(
                "SELECT id, display_name, scope, kind, corpus_version FROM projects "
                "ORDER BY display_name, id"
            ).fetchall()
        if not project_rows:
            parts.append("<p>No projects.</p></body></html>")
            return "\n".join(parts)

        parts.append("<h2>Projects</h2><table><tr><th>Name</th><th>Scope</th><th>Kind</th><th>Corpus</th><th>Id</th></tr>")
        for project in project_rows:
            pid = str(project["id"])
            name = _esc(project["display_name"])
            href = f"/project/{_esc(pid)}" if PROJECT_ID_RE.fullmatch(pid) else "#"
            parts.append(
                "<tr>"
                f"<td><a href=\"{href}\">{name}</a></td>"
                f"<td>{_esc(project['scope'])}</td>"
                f"<td>{_esc(project['kind'])}</td>"
                f"<td>{_esc(project['corpus_version'])}</td>"
                f"<td><code>{_esc(pid)}</code></td>"
                "</tr>"
            )
        parts.append("</table>")

        for project in project_rows:
            pid = str(project["id"])
            parts.append(f"<h2 id=\"p-{_esc(pid)}\">{_esc(project['display_name'])}</h2>")
            parts.append(
                f"<p>scope={_esc(project['scope'])} kind={_esc(project['kind'])} "
                f"corpus_version={_esc(project['corpus_version'])}</p>"
            )

            sources = connection.execute(
                "SELECT id, resolved_path, relative_path, current_hash, active "
                "FROM sources WHERE project_id=? ORDER BY relative_path, id",
                (pid,),
            ).fetchall()
            parts.append("<h3>Sources</h3>")
            parts.append(
                "<table><tr><th>Id</th><th>Path</th><th>Hash</th><th>Active</th></tr>"
            )
            for source in sources:
                parts.append(
                    "<tr>"
                    f"<td>{_esc(source['id'])}</td>"
                    f"<td><code>{_esc(source['relative_path'])}</code><br>"
                    f"<code>{_esc(source['resolved_path'])}</code></td>"
                    f"<td><code>{_esc(source['current_hash'])}</code></td>"
                    f"<td>{'yes' if int(source['active'] or 0) else 'no'}</td>"
                    "</tr>"
                )
            parts.append("</table>")

            chunks = connection.execute(
                """
                SELECT c.id AS chunk_id, c.text, c.heading, c.line_start, c.line_end,
                       c.chunk_hash, c.ordinal, s.relative_path, s.resolved_path,
                       s.current_hash AS source_hash, s.active, v.content_hash
                FROM chunks c
                JOIN source_versions v ON v.id=c.source_version_id
                JOIN sources s ON s.id=v.source_id
                WHERE s.project_id=?
                ORDER BY s.relative_path, c.ordinal, c.id
                """,
                (pid,),
            ).fetchall()
            parts.append("<h3>Chunks (pointers)</h3>")
            parts.append(
                "<table><tr><th>Id</th><th>Path</th><th>Lines</th><th>Hash</th>"
                "<th>Stale</th><th>Text</th></tr>"
            )
            for chunk in chunks:
                row = hydrate_chunk_row(dict(chunk), cache)
                stale = bool(row.get("stale"))
                stale_cell = '<span class="stale">yes</span>' if stale else "no"
                plate = row.get("text") or ""
                parts.append(
                    "<tr>"
                    f"<td>{_esc(row['chunk_id'])}</td>"
                    f"<td><code>{_esc(row['relative_path'])}</code></td>"
                    f"<td>{_esc(row['line_start'])}–{_esc(row['line_end'])}</td>"
                    f"<td><code>{_esc(row['chunk_hash'])}</code></td>"
                    f"<td>{stale_cell}</td>"
                    f"<td><pre>{_esc(plate)}</pre></td>"
                    "</tr>"
                )
            parts.append("</table>")

            claims = connection.execute(
                "SELECT id, display_text, trust_state, created_by FROM claims "
                "WHERE project_id=? ORDER BY updated_at DESC, id",
                (pid,),
            ).fetchall()
            parts.append("<h3>Claims</h3>")
            parts.append(
                "<table><tr><th>Id</th><th>State</th><th>By</th><th>Text</th></tr>"
            )
            for claim in claims:
                parts.append(
                    "<tr>"
                    f"<td><code>{_esc(claim['id'])}</code></td>"
                    f"<td>{_esc(claim['trust_state'])}</td>"
                    f"<td>{_esc(claim['created_by'])}</td>"
                    f"<td>{_esc(claim['display_text'])}</td>"
                    "</tr>"
                )
            parts.append("</table>")

            validations = connection.execute(
                "SELECT v.id, v.claim_id, v.validator, v.result, v.observed_at "
                "FROM validations v JOIN claims c ON c.id=v.claim_id "
                "WHERE c.project_id=? ORDER BY v.observed_at, v.id",
                (pid,),
            ).fetchall()
            parts.append("<h3>Validations</h3>")
            parts.append(
                "<table><tr><th>Id</th><th>Claim</th><th>Validator</th><th>Result</th><th>At</th></tr>"
            )
            for item in validations:
                parts.append(
                    "<tr>"
                    f"<td>{_esc(item['id'])}</td>"
                    f"<td><code>{_esc(item['claim_id'])}</code></td>"
                    f"<td>{_esc(item['validator'])}</td>"
                    f"<td>{_esc(item['result'])}</td>"
                    f"<td>{_esc(item['observed_at'])}</td>"
                    "</tr>"
                )
            parts.append("</table>")

    parts.append("</body></html>")
    return "\n".join(parts)


class AdminHandler(BaseHTTPRequestHandler):
    database: Database

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        project_id = None
        if path in ("/", "/index.html"):
            project_id = None
        else:
            match = re.fullmatch(r"/project/([^/]+)", path)
            if not match:
                self.send_error(404, "Not found")
                return
            project_id = _bound_project_id(match.group(1))
            if project_id is None:
                self.send_error(404, "Not found")
                return
        try:
            body = build_admin_html(self.database, project_id=project_id)
        except Exception:
            self.send_error(500, "Failed to build page")
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        self.send_error(405, "Read-only")

    def log_message(self, format: str, *args) -> None:
        return


def serve_admin(database: Database, port: int = 7432) -> None:
    handler = type("BoundAdminHandler", (AdminHandler,), {"database": database})
    server = HTTPServer(("127.0.0.1", int(port)), handler)
    url = f"http://127.0.0.1:{int(port)}/"
    print(url, flush=True)
    server.serve_forever()
