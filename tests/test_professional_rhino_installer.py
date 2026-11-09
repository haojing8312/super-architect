"""Operator report guards only: no real download, installer or host is run."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts/professional-rhino-installer.py"
# `scripts/` is intentionally ignored in the public source snapshot. This is an
# operator-facing professional-host installer guard from the historical build
# environment, not an account/cloud path and not part of the M1 local runtime.
APPLICABLE = SCRIPT.is_file()
if APPLICABLE:
    SPEC = importlib.util.spec_from_file_location("rhino_installer_guard", SCRIPT)
    INSTALLER = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(INSTALLER)
else:
    INSTALLER = None


@unittest.skipUnless(APPLICABLE, "operator Rhino installer script is not part of the public checkout; not applicable to M1 local runtime")
class RhinoReportGuardTests(unittest.TestCase):
    def setUp(self):
        own_root = (PROJECT / ".local/rhino-installer-guard-tests").resolve()
        own_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="report-", dir=own_root)
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(own_root))
        self.addCleanup(self.temporary.cleanup)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.state = self.root / "state"
        self.evidence = self.root / "evidence/native-installation.json"
        self.evidence.parent.mkdir(parents=True)
        self.target = self.root / "new-program"
        self.package = self.cache / INSTALLER.NAME
        self.package.write_bytes(b"synthetic signed package fixture")
        self.digest = hashlib.sha256(self.package.read_bytes()).hexdigest()
        self.patches = patch.multiple(INSTALLER, ROOT=self.root, CACHE=self.cache, STATE=self.state,
                                      EVIDENCE=self.evidence, TARGET=self.target,
                                      SIZE=self.package.stat().st_size, SHA256=self.digest)
        self.patches.start()
        self.addCleanup(self.patches.stop)
        self.signature = {"status": "Valid", "subject": "Synthetic McNeel signer for this mocked operator test"}
        self.signer = patch.object(INSTALLER, "verify_signature", return_value=self.signature)
        self.signature_mock = self.signer.start()
        self.addCleanup(self.signer.stop)
        self.native = patch.object(INSTALLER, "install", side_effect=AssertionError("A real/native installer must never run in these tests"))
        self.native_mock = self.native.start()
        self.addCleanup(self.native.stop)
        self.download = patch.object(INSTALLER, "fetch", side_effect=AssertionError("A network download must never run in these tests"))
        self.download_mock = self.download.start()
        self.addCleanup(self.download.stop)

    def prior_success(self, **changes):
        record = {
            "host": "Rhino 8", "version": INSTALLER.VERSION, "sha256": self.digest,
            "installation_directory": str(self.target), "checked_at_utc": "prior-native-checkpoint",
            "installed": True, "installer_executed": True, "installation_exit_code": 0,
            "owned_bootstrapper_pid": 12345, "installation_status": "installed-license-not-activated",
            "installed_executable": str(self.target / "System/Rhino.exe"),
            "installed_executable_verification": {"sha256": "synthetic-executable-sha", "version": INSTALLER.VERSION},
            "installed_registry": {"products": ["synthetic Rhino 8"], "install_locations": [str(self.target)]},
            "installed_msi_product_states": [{"product_code": "synthetic-product", "state": 5}],
            "executable_version_verified": True, "registry_location_verified": True,
            "product_registration_verified": True, "native_product_state_verified": True,
            "licensed_host_ready": True, "professional_model_tools_verified": True,
            "gui_ready": True, "owned_file_count": 1185,
        }
        record.update(changes)
        self.evidence.write_text(json.dumps(record), encoding="utf-8")
        return record

    def invoke(self, *arguments):
        with patch("sys.argv", ["professional-rhino-installer.py", *arguments]), redirect_stdout(io.StringIO()):
            INSTALLER.main()
        self.native_mock.assert_not_called()
        self.download_mock.assert_not_called()
        return json.loads(self.evidence.read_text(encoding="utf-8"))

    def test_artifact_only_without_native_result_stays_uninstalled(self):
        result = self.invoke()
        self.assertFalse(result["installed"])
        self.assertFalse(result["installer_executed"])
        self.assertFalse(result["licensed_host_ready"])
        self.assertTrue(result["artifact_verification"]["verified"])
        self.assertFalse(result["artifact_verification"]["native_result_preserved"])
        self.signature_mock.assert_called_once_with(self.package)

    def test_default_verification_preserves_matching_native_success_and_history(self):
        previous = self.prior_success()
        old_payload = self.evidence.read_bytes()
        result = self.invoke()
        for name, value in previous.items():
            self.assertEqual(result[name], value, name)
        self.assertTrue(result["artifact_verification"]["verified"])
        self.assertTrue(result["artifact_verification"]["native_result_preserved"])
        self.assertNotEqual(result["artifact_verification"]["checked_at_utc"], previous["checked_at_utc"])
        archives = list(self.state.glob("prior-result-*.json"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), old_payload)
        self.signature_mock.assert_called_once_with(self.package)

    def test_fetch_flag_still_checks_artifact_and_keeps_matching_native_fields(self):
        previous = self.prior_success()
        result = self.invoke("--fetch")
        self.assertTrue(result["installed"])
        self.assertEqual(result["installed_registry"], previous["installed_registry"])
        self.assertEqual(result["installation_exit_code"], 0)
        self.signature_mock.assert_called_once_with(self.package)

    def test_different_version_never_inherits_native_result(self):
        self.prior_success(version="8.34.other")
        self.assert_new_artifact(self.invoke())

    def test_different_package_hash_never_inherits_native_result(self):
        self.prior_success(sha256="0" * 64)
        self.assert_new_artifact(self.invoke())

    def test_different_host_never_inherits_native_result(self):
        self.prior_success(host="Other host")
        self.assert_new_artifact(self.invoke())

    def test_different_program_target_never_inherits_native_result(self):
        self.prior_success(installation_directory=str(self.root / "other-program"))
        self.assert_new_artifact(self.invoke())

    def assert_new_artifact(self, result):
        self.assertFalse(result["installed"])
        self.assertFalse(result["installer_executed"])
        self.assertFalse(result["artifact_verification"]["native_result_preserved"])
        self.assertNotIn("installed_executable", result)
        self.assertNotIn("installed_registry", result)
        self.assertNotIn("installation_exit_code", result)

    def test_prior_success_does_not_bypass_real_package_sha_check(self):
        self.prior_success()
        old_payload = self.evidence.read_bytes()
        with patch.object(INSTALLER, "file_sha256", return_value="f" * 64):
            with self.assertRaisesRegex(ValueError, "SHA256 pin"):
                self.invoke()
        self.assertEqual(self.evidence.read_bytes(), old_payload)
        failures = list(self.state.glob("artifact-verification-failed-*.json"))
        self.assertEqual(len(failures), 1)
        self.assertFalse(json.loads(failures[0].read_text())["artifact_verification"]["verified"])
        self.native_mock.assert_not_called()
        self.signature_mock.assert_called_once_with(self.package)

    def test_prior_success_does_not_bypass_native_signature_validation(self):
        self.prior_success()
        old_payload = self.evidence.read_bytes()
        self.signature_mock.side_effect = ValueError("Synthetic invalid native signature")
        with self.assertRaisesRegex(ValueError, "invalid native signature"):
            self.invoke("--fetch")
        self.assertEqual(self.evidence.read_bytes(), old_payload)
        self.assertEqual(len(list(self.state.glob("artifact-verification-failed-*.json"))), 1)
        self.native_mock.assert_not_called()
        self.signature_mock.assert_called_once_with(self.package)


if __name__ == "__main__":
    unittest.main()
