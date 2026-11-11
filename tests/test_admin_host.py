from __future__ import annotations

import http.client
from http.server import HTTPServer
import tempfile
import threading
import unittest
from pathlib import Path

from rso_context.admin import AdminHandler
from rso_context.db import Database


class AdminHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        database = Database(Path(self.tmp.name) / "context.sqlite3")
        database.initialize()
        handler = type("BoundAdminHandler", (AdminHandler,), {"database": database})
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def status(self, host: str | None) -> int:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.putrequest("GET", "/", skip_host=True)
            if host is not None:
                connection.putheader("Host", host)
            connection.endheaders()
            return connection.getresponse().status
        finally:
            connection.close()

    def test_loopback_hosts_with_the_bound_port_are_served(self):
        self.assertEqual(self.status(f"127.0.0.1:{self.port}"), 200)
        self.assertEqual(self.status(f"localhost:{self.port}"), 200)
        self.assertEqual(self.status(f"[::1]:{self.port}"), 200)

    def test_foreign_host_wrong_port_and_missing_host_are_refused(self):
        self.assertEqual(self.status("evil.example"), 403)
        self.assertEqual(self.status(f"evil.example:{self.port}"), 403)
        self.assertEqual(self.status(f"127.0.0.1:{self.port + 1}"), 403)
        self.assertEqual(self.status(None), 403)


if __name__ == "__main__":
    unittest.main()
