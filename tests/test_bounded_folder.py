"""The bounded-folder rule: Windows spellings, profile children, system folders, CLI."""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rso_context.cli import main
from rso_context.mcp_contract import (
    PROFILE_CONTAINERS,
    _key,
    _wsl_refusal,
    assert_bounded_root,
    bounded_folder_refusal,
    normalize_path_text,
    resolve_bounded,
)

HOME = Path.home().resolve()
ON_WINDOWS = os.name == "nt"
PROJECT = Path(__file__).resolve().parents[1]


def refusal(path: str | Path) -> str | None:
    return bounded_folder_refusal(resolve_bounded(path))


def admin_share(path: Path, host: str = "localhost") -> str:
    drive, rest = os.path.splitdrive(str(path))
    return "\\\\" + host + "\\" + drive[0] + "$" + rest


@unittest.skipUnless(ON_WINDOWS, "Windows path spellings")
class WindowsSpellings(unittest.TestCase):
    def test_extended_length_profile(self):
        self.assertEqual(refusal("\\\\?\\" + str(HOME)), "a user profile")

    def test_extended_length_documents(self):
        self.assertEqual(refusal("\\\\?\\" + str(HOME / "Documents")), "an unbounded folder")

    def test_admin_share_profile(self):
        self.assertEqual(refusal(admin_share(HOME)), "a user profile")

    def test_admin_share_ssh(self):
        self.assertEqual(refusal(admin_share(HOME / ".ssh")), "a credentials folder")

    def test_admin_share_windows(self):
        windows = Path(os.environ.get("SystemRoot", "C:\\Windows"))
        self.assertEqual(refusal(admin_share(windows)), "an operating-system folder")

    def test_extended_unc_admin_share(self):
        drive, rest = os.path.splitdrive(str(HOME))
        self.assertEqual(refusal("\\\\?\\UNC\\localhost\\" + drive[0] + "$" + rest), "a user profile")

    def test_loopback_address_share(self):
        self.assertEqual(refusal(admin_share(HOME, "127.0.0.1")), "a user profile")

    def test_forward_slash_spelling(self):
        self.assertEqual(refusal(admin_share(HOME).replace("\\", "/")), "a user profile")

    def test_this_computers_own_name_folds_to_the_drive(self):
        host = os.environ.get("COMPUTERNAME") or socket.gethostname()
        self.assertEqual(refusal(admin_share(HOME, host)), "a user profile")
        self.assertEqual(refusal(admin_share(HOME / ".ssh", host.lower())), "a credentials folder")

    def test_named_share_on_this_computer_is_refused_even_for_a_launch_root(self):
        for host in ("localhost", os.environ.get("COMPUTERNAME") or socket.gethostname()):
            path = Path("\\\\" + host + "\\Users\\someone\\.ssh")
            for allow in (False, True):
                with self.subTest(host=host, allow=allow):
                    self.assertEqual(
                        bounded_folder_refusal(path, allow_network=allow), "a network path to this computer"
                    )

    def test_mapped_drive_resolving_to_a_share_stays_allowed(self):
        share = Path("\\\\srv\\share\\proj")
        self.assertIsNone(bounded_folder_refusal(share, typed="Z:\\proj"))
        self.assertEqual(bounded_folder_refusal(Path("\\\\srv\\share"), typed="Z:\\"), "a filesystem root")
        self.assertIsNone(bounded_folder_refusal(share, typed="\\\\srv\\share\\proj"))
        self.assertIsNone(bounded_folder_refusal(share))
        real = resolve_bounded

        def fake(value):
            return share if str(value) == "Z:\\proj" else real(value)

        with patch("rso_context.mcp_contract.resolve_bounded", side_effect=fake), patch.object(
            Path, "is_dir", return_value=True
        ):
            self.assertEqual(assert_bounded_root("Z:\\proj"), share)

    def test_setup_binds_a_network_root(self):
        from rso_context import mcp_setup

        with patch.object(mcp_setup, "bind_roots", side_effect=RuntimeError("stop")) as bound:
            with self.assertRaises(RuntimeError):
                mcp_setup.setup_client(next(iter(mcp_setup.CLIENTS)), ["\\\\srv\\share\\proj"], config="unused.json")
        self.assertTrue(bound.call_args.kwargs["allow_network"])

    def test_normalization(self):
        self.assertEqual(normalize_path_text("\\\\?\\C:\\x"), "C:\\x")
        self.assertEqual(normalize_path_text("\\\\?\\UNC\\host\\share\\x"), "\\\\host\\share\\x")
        self.assertEqual(normalize_path_text("\\\\localhost\\d$\\x"), "d:\\x")
        self.assertEqual(normalize_path_text("\\\\localhost\\D$"), "D:\\")

    def test_remote_share_folder_is_allowed_however_it_was_typed(self):
        for kwargs in ({}, {"allow_network": True}, {"typed": "Z:\\proj"}, {"typed": "\\\\fileserver\\share\\proj"}):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(bounded_folder_refusal(Path("\\\\fileserver\\share\\proj\\src"), **kwargs))

    def test_stored_alias_form_of_a_mapped_drive_is_allowed(self):
        self.assertIsNone(bounded_folder_refusal(Path("\\\\TheBrain\\TheBrain\\AI_Pipeline_Tool")))
        with patch.object(Path, "is_dir", return_value=True):
            self.assertEqual(
                assert_bounded_root("\\\\TheBrain\\TheBrain\\AI_Pipeline_Tool"),
                Path("\\\\TheBrain\\TheBrain\\AI_Pipeline_Tool"),
            )

    def test_share_root_is_a_filesystem_root(self):
        self.assertEqual(bounded_folder_refusal(Path("\\\\fileserver\\share")), "a filesystem root")
        self.assertEqual(bounded_folder_refusal(Path("\\\\fileserver\\share\\")), "a filesystem root")

    def test_administrative_and_hidden_shares_are_refused(self):
        for share in ("c$", "ADMIN$", "ipc$", "data$"):
            for allow in (False, True):
                with self.subTest(share=share, allow=allow):
                    self.assertEqual(
                        bounded_folder_refusal(Path("\\\\fileserver\\" + share + "\\proj"), allow_network=allow),
                        "an administrative share",
                    )

    def test_device_paths_are_refused(self):
        self.assertEqual(bounded_folder_refusal(Path("\\\\.\\PhysicalDrive0\\x")), "a network or device path")

    def test_this_computers_ip_addresses_are_refused(self):
        with patch("rso_context.mcp_contract._local_addresses", return_value=frozenset({"192.168.7.7"})):
            self.assertEqual(
                bounded_folder_refusal(Path("\\\\192.168.7.7\\share\\proj")), "a network path to this computer"
            )
        self.assertIsNone(bounded_folder_refusal(Path("\\\\192.168.7.8\\share\\proj")))

    def test_remote_profile_copies_are_not_served_whole(self):
        base = "\\\\fileserver\\share\\"
        cases = {
            "Users": "a folder that contains a user profile",
            "Users\\bob": "a user profile",
            "Users\\bob\\.ssh": "a credentials folder",
            "Users\\bob\\Documents": "an unbounded folder",
            "backup\\home\\bob\\AppData\\Roaming\\x": "an application data folder",
            "Users\\bob\\work\\proj": None,
            "projects\\proj": None,
        }
        for tail, expected in cases.items():
            with self.subTest(tail=tail):
                self.assertEqual(bounded_folder_refusal(Path(base + tail)), expected)

    def test_assert_bounded_root_uses_normalization(self):
        with self.assertRaises(ValueError):
            assert_bounded_root("\\\\?\\" + str(HOME))

    def test_ordinary_drive_path_still_normal(self):
        self.assertEqual(normalize_path_text(str(PROJECT)), str(PROJECT))


class NormalizationIsPortable(unittest.TestCase):
    @unittest.skipIf(ON_WINDOWS, "POSIX no-op")
    def test_noop_on_posix(self):
        self.assertEqual(normalize_path_text("//localhost/c$/x"), "//localhost/c$/x")


class ProfileChildren(unittest.TestCase):
    def test_profile_itself(self):
        self.assertEqual(refusal(HOME), "a user profile")

    @unittest.skipUnless(ON_WINDOWS, "AppData is a Windows profile folder")
    def test_appdata_and_children(self):
        for tail in ("AppData", "AppData/Local", "AppData/Roaming", "AppData/LocalLow"):
            with self.subTest(tail=tail):
                self.assertIsNotNone(refusal(HOME / tail))

    @unittest.skipUnless(ON_WINDOWS, "AppData is a Windows profile folder")
    def test_deep_under_roaming(self):
        self.assertIsNotNone(refusal(HOME / "AppData" / "Roaming" / "Vendor" / "app" / "data"))

    @unittest.skipUnless(ON_WINDOWS, "AppData is a Windows profile folder")
    def test_appdata_is_refused_at_any_depth_except_temp(self):
        local = HOME / "AppData" / "Local"
        for tail in ("SomeProject", "Microsoft/Credentials", "Google/Chrome/User Data/Default", "Temp"):
            with self.subTest(tail=tail):
                self.assertEqual(refusal(local / tail), "an application data folder")
        self.assertIsNone(refusal(local / "Temp" / "made-by-a-test"))
        temp = Path(tempfile.gettempdir()).resolve()
        self.assertIsNotNone(refusal(temp))
        self.assertIsNone(refusal(temp / "project"))

    def test_sibling_profile_children(self):
        if HOME.parent.name.casefold() not in PROFILE_CONTAINERS:
            self.skipTest("home is not inside a profile container")
        other = HOME.parent / "SomeoneElse"
        self.assertEqual(bounded_folder_refusal(other), "a user profile")
        self.assertEqual(bounded_folder_refusal(other / ".ssh"), "a credentials folder")
        self.assertEqual(bounded_folder_refusal(other / "Documents"), "an unbounded folder")
        self.assertEqual(bounded_folder_refusal(other / "AppData" / "Roaming" / "x"), "an application data folder")
        self.assertIsNone(bounded_folder_refusal(other / "work" / "proj"))

    def test_onedrive_known_folders(self):
        for name in ("OneDrive", "OneDrive - Company"):
            for known in ("Documents", "Desktop", "Downloads"):
                with self.subTest(name=name, known=known):
                    self.assertEqual(refusal(HOME / name / known), "an unbounded folder")
        self.assertIsNone(refusal(HOME / "OneDrive" / "proj"))

    def test_case_is_folded_on_macos(self):
        with patch("sys.platform", "darwin"):
            self.assertEqual(bounded_folder_refusal(Path(str(HOME).upper())), "a user profile")
            self.assertEqual(str(_key(Path("/Users/Paul"))), str(Path("/users/paul")))

    def test_secret_folders_at_depth(self):
        for tail in (".ssh/x", ".aws/sso", ".aws/sso/cache", ".gnupg/private-keys-v1.d", ".kube/cache", ".config/gcloud/logs"):
            with self.subTest(tail=tail):
                self.assertEqual(refusal(HOME / tail), "a credentials folder")

    def test_hidden_child_still_refused_but_deep_allowed(self):
        self.assertEqual(refusal(HOME / ".local"), "a hidden profile folder")
        self.assertIsNone(refusal(HOME / ".local" / "some-project"))

    def test_unbounded_children(self):
        for name in ("Desktop", "Documents", "Downloads", "OneDrive", "OneDrive - Work"):
            with self.subTest(name=name):
                self.assertEqual(refusal(HOME / name), "an unbounded folder")

    def test_this_project_is_allowed(self):
        self.assertIsNone(refusal(PROJECT))
        self.assertEqual(assert_bounded_root(PROJECT), PROJECT)

    @unittest.skipUnless(sys.platform == "darwin", "macOS Library")
    def test_macos_library(self):
        self.assertIsNotNone(refusal(HOME / "Library"))
        self.assertIsNotNone(refusal(HOME / "Library" / "Application Support"))


class TempFolder(unittest.TestCase):
    def test_temp_root_refused_and_subfolder_allowed(self):
        temp = Path(tempfile.gettempdir()).resolve()
        self.assertIsNotNone(refusal(temp))
        with tempfile.TemporaryDirectory() as made:
            self.assertIsNone(refusal(made))
        self.assertIsNone(refusal(temp / "some-project"))


class SystemFolders(unittest.TestCase):
    @unittest.skipUnless(ON_WINDOWS, "Windows system folders")
    def test_without_environment(self):
        saved = {name: os.environ.get(name) for name in ("SystemRoot", "ProgramFiles")}
        names = ("SystemRoot", "SYSTEMROOT", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
        with patch.dict(os.environ):
            for name in names:
                os.environ.pop(name, None)
            self.assertEqual(refusal(saved["SystemRoot"] or "C:\\Windows"), "an operating-system folder")
            self.assertEqual(refusal(saved["ProgramFiles"] or "C:\\Program Files"), "an operating-system folder")

    @unittest.skipIf(ON_WINDOWS, "POSIX system folders")
    def test_posix_folders(self):
        for name in ("/usr", "/usr/share", "/etc", "/lib", "/var/log"):
            if Path(name).is_dir():
                with self.subTest(name=name):
                    self.assertIsNotNone(refusal(name))

    @unittest.skipIf(ON_WINDOWS, "POSIX temp stays allowed")
    def test_var_and_tmp_subfolders_stay_allowed(self):
        with tempfile.TemporaryDirectory() as made:
            self.assertIsNone(refusal(made))

    def test_wsl_windows_drive_rules(self):
        base = ("/", "mnt", "c")
        cases = {
            ("Users",): "a folder that contains a user profile",
            ("Users", "u"): "a user profile",
            ("Users", "u", ".ssh"): "a credentials folder",
            ("Users", "u", ".ssh", "keys"): "a credentials folder",
            ("Users", "u", "Documents"): "an unbounded folder",
            ("Users", "u", "AppData", "Roaming", "x"): "an application data folder",
            ("Users", "u", "OneDrive", "Desktop"): "an unbounded folder",
            ("Windows",): "an operating-system folder",
            ("Windows", "System32"): "an operating-system folder",
            ("Program Files", "x"): "an operating-system folder",
            ("ProgramData",): "an operating-system folder",
            ("src", "proj"): None,
            ("Users", "u", "work", "proj"): None,
        }
        for rest, expected in cases.items():
            with self.subTest(rest=rest):
                self.assertEqual(_wsl_refusal(base + rest), expected)
        self.assertIsNone(_wsl_refusal(("/", "home", "c", "Windows")))

    @unittest.skipIf(ON_WINDOWS, "POSIX folders that hold drive mounts")
    def test_exact_mount_parents_refused(self):
        for name in ("/mnt", "/media", "/run/media"):
            with self.subTest(name=name):
                self.assertEqual(bounded_folder_refusal(Path(name)), "a folder that holds drive mounts")

    @unittest.skipIf(ON_WINDOWS, "POSIX system folders")
    def test_container_and_ci_projects_stay_allowed(self):
        for name in ("/usr/src/app", "/usr/local/src/x", "/var/lib/jenkins/workspace/x", "/workspaces/proj"):
            with self.subTest(name=name):
                self.assertIsNone(bounded_folder_refusal(Path(name)))
        # Resolved first, as every caller does: on macOS /var is a link to /private/var.
        for name in ("/usr", "/var", "/usr/bin", "/usr/share/doc", "/var/log"):
            with self.subTest(name=name):
                self.assertEqual(bounded_folder_refusal(Path(name).resolve()), "an operating-system folder")

    def test_drive_root(self):
        self.assertEqual(refusal(Path(PROJECT.anchor)), "a filesystem root")

    @unittest.skipIf(ON_WINDOWS, "POSIX mount points")
    def test_mount_point_refused_only_in_drive_locations(self):
        with tempfile.TemporaryDirectory() as made, patch("os.path.ismount", return_value=True):
            self.assertIsNone(refusal(made))
        for name in ("/mnt/data", "/media/usb", "/media/me/usb", "/run/media/me/usb", "/Volumes/Backup"):
            with self.subTest(name=name), patch("os.path.ismount", return_value=True):
                self.assertEqual(bounded_folder_refusal(Path(name)), "a filesystem root")
        with patch("os.path.ismount", return_value=False):
            self.assertIsNone(bounded_folder_refusal(Path("/media/usb")))

    @unittest.skipUnless(sys.platform.startswith("linux"), "WSL drive mounts")
    def test_wsl_drive_letter_refused_without_mount(self):
        with patch("os.path.ismount", return_value=False):
            self.assertEqual(bounded_folder_refusal(Path("/mnt/c")), "a filesystem root")


class CommandLine(unittest.TestCase):
    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_use_refuses_profile_and_names_flag(self):
        with tempfile.TemporaryDirectory() as data:
            db = str(Path(data) / "ledger.sqlite3")
            code, out, err = self.run_cli("--db", db, "use", "--agent", "bounded-test", "--path", str(HOME))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        message = json.loads(err)["message"]
        self.assertIn("a user profile", message)
        self.assertIn("--allow-broad", message)

    def test_register_and_ingest_and_watch_refuse_profile(self):
        with tempfile.TemporaryDirectory() as data:
            db = str(Path(data) / "ledger.sqlite3")
            for command in (["register", "--agent", "t"], ["ingest"], ["watch", "--once"]):
                with self.subTest(command=command[0]):
                    code, _out, _err = self.run_cli("--db", db, *command, "--path", str(HOME))
                    self.assertEqual(code, 2)

    def test_explain_project_refuses_profile_without_registering(self):
        with tempfile.TemporaryDirectory() as data:
            db = str(Path(data) / "ledger.sqlite3")
            code, _out, err = self.run_cli("--db", db, "explain-project", "--path", str(HOME))
            self.assertEqual(code, 2)
            self.assertIn("--allow-broad", json.loads(err)["message"])
            code, out, _err = self.run_cli("--db", db, "ingest", "--all")
            self.assertEqual(json.loads(out)["count"], 0)

    def test_stored_broad_aliases_are_skipped_by_ingest_all_and_watch(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as empty_root:
            db = str(Path(data) / "ledger.sqlite3")
            code, _out, _err = self.run_cli(
                "--db", db, "register", "--agent", "t", "--allow-broad", "--path", str(HOME)
            )
            self.assertEqual(code, 0)
            code, out, _err = self.run_cli("--db", db, "ingest", "--all")
            summary = json.loads(out)
            self.assertEqual((code, summary["count"]), (0, 0))
            self.assertEqual([p.casefold() for p in summary["skipped_broad"]], [str(HOME).casefold()])
            code, out, _err = self.run_cli("--db", db, "watch", "--once", empty_root)
            summary = json.loads(out)
            self.assertEqual((code, summary["projects"]), (0, 0))
            self.assertEqual([p.casefold() for p in summary["skipped_broad"]], [str(HOME).casefold()])

    def test_temp_project_still_ingests(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as project:
            (Path(project) / "AGENTS.md").write_text("# Fixture\n", encoding="utf-8")
            db = str(Path(data) / "ledger.sqlite3")
            code, out, _err = self.run_cli("--db", db, "use", "--agent", "bounded-test", "--path", project)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["schema"], "rso-context-use/v1")


if __name__ == "__main__":
    unittest.main()
