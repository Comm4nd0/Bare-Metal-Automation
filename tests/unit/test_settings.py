"""Tests for the centralised settings module — env var overrides and defaults."""

from __future__ import annotations

import importlib
from unittest.mock import patch

# ── Default values ───────────────────────────────────────────────────────


class TestDefaults:
    def test_ilo_default_user(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.ILO_DEFAULT_USER == "Administrator"

    def test_ilo_default_password(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.ILO_DEFAULT_PASSWORD == "admin"

    def test_meinberg_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.MEINBERG_DEFAULT_USER == "admin"
            assert mod.MEINBERG_DEFAULT_PASSWORD == ""

    def test_default_credentials(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.DEFAULT_CREDENTIALS == [
                ("cisco", "cisco"),
                ("admin", "admin"),
                ("admin", ""),
            ]

    def test_redfish_paths(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.REDFISH_BASE == "/redfish/v1"
            assert mod.REDFISH_SYSTEMS == "/redfish/v1/Systems/1"
            assert mod.REDFISH_MANAGERS == "/redfish/v1/Managers/1"
            assert mod.REDFISH_UPDATE == "/redfish/v1/UpdateService"

    def test_timeout_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.LONG_OP_TIMEOUT == 3600
            assert mod.POLL_INTERVAL == 30
            assert mod.SSH_TIMEOUT == 30
            assert mod.RELOAD_TIMER_MINUTES == 5
            assert mod.REBOOT_WAIT == 120
            assert mod.POLL_TIMEOUT == 300

    def test_verify_ssl_default_false(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.VERIFY_SSL is False

    def test_file_path_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.FIRMWARE_DIR == "configs/firmware"
            assert mod.TEMPLATE_DIR == "configs/templates"
            assert mod.ISO_DIR == "configs/iso"
            assert mod.INVENTORY_DIR == "configs/inventory"
            assert mod.LEASE_FILE == "/var/lib/misc/dnsmasq.leases"


# ── Environment variable overrides ──────────────────────────────────────


class TestEnvOverrides:
    def test_ilo_user_override(self) -> None:
        with patch.dict("os.environ", {"BMA_ILO_USER": "custom_admin"}):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.ILO_DEFAULT_USER == "custom_admin"

    def test_ilo_password_override(self) -> None:
        with patch.dict("os.environ", {"BMA_ILO_PASSWORD": "supersecret"}):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.ILO_DEFAULT_PASSWORD == "supersecret"

    def test_meinberg_user_override(self) -> None:
        with patch.dict("os.environ", {"BMA_MEINBERG_USER": "ntproot"}):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.MEINBERG_DEFAULT_USER == "ntproot"

    def test_long_op_timeout_override(self) -> None:
        with patch.dict("os.environ", {"BMA_LONG_OP_TIMEOUT": "7200"}):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.LONG_OP_TIMEOUT == 7200

    def test_poll_interval_override(self) -> None:
        with patch.dict("os.environ", {"BMA_POLL_INTERVAL": "10"}):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.POLL_INTERVAL == 10

    def test_verify_ssl_true(self) -> None:
        with patch.dict("os.environ", {"BMA_VERIFY_SSL": "1"}):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.VERIFY_SSL is True

    def test_verify_ssl_true_word(self) -> None:
        with patch.dict("os.environ", {"BMA_VERIFY_SSL": "true"}):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.VERIFY_SSL is True

    def test_verify_ssl_yes(self) -> None:
        with patch.dict("os.environ", {"BMA_VERIFY_SSL": "yes"}):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.VERIFY_SSL is True

    def test_lease_file_override(self) -> None:
        with patch.dict("os.environ", {"BMA_LEASE_FILE": "/tmp/leases"}):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.LEASE_FILE == "/tmp/leases"

    def test_firmware_dir_override(self) -> None:
        with patch.dict("os.environ", {"BMA_FIRMWARE_DIR": "/opt/firmware"}):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.FIRMWARE_DIR == "/opt/firmware"


# ── Discovery credential parsing ────────────────────────────────────────


class TestCredentialParsing:
    def test_parse_custom_credentials(self) -> None:
        with patch.dict(
            "os.environ",
            {"BMA_DISCOVERY_CREDENTIALS": "root:toor,admin:password123"},
        ):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.DEFAULT_CREDENTIALS == [
                ("root", "toor"),
                ("admin", "password123"),
            ]

    def test_parse_credential_with_empty_password(self) -> None:
        with patch.dict(
            "os.environ",
            {"BMA_DISCOVERY_CREDENTIALS": "admin:"},
        ):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.DEFAULT_CREDENTIALS == [("admin", "")]

    def test_parse_credential_with_colon_in_password(self) -> None:
        with patch.dict(
            "os.environ",
            {"BMA_DISCOVERY_CREDENTIALS": "admin:pass:word"},
        ):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            # split(":", 1) should keep "pass:word" as the password
            assert mod.DEFAULT_CREDENTIALS == [("admin", "pass:word")]

    def test_parse_malformed_entries_skipped(self) -> None:
        with patch.dict(
            "os.environ",
            {"BMA_DISCOVERY_CREDENTIALS": "good:cred,badentry,also:good"},
        ):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            assert mod.DEFAULT_CREDENTIALS == [
                ("good", "cred"),
                ("also", "good"),
            ]

    def test_empty_credential_string_uses_defaults(self) -> None:
        with patch.dict(
            "os.environ",
            {"BMA_DISCOVERY_CREDENTIALS": ""},
        ):
            import bare_metal_automation.settings as settings
            mod = importlib.reload(settings)

            # Empty string is falsy, so defaults should be used
            assert mod.DEFAULT_CREDENTIALS == [
                ("cisco", "cisco"),
                ("admin", "admin"),
                ("admin", ""),
            ]
