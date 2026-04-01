"""Tests for the driver registry and base classes."""

from __future__ import annotations

import pytest

from bare_metal_automation.drivers import (
    ApplianceDriver,
    DriverRegistry,
    NetworkDriver,
    ServerDriver,
    load_builtin_drivers,
)
from bare_metal_automation.drivers.base import DiscoveryDriver

# ── Concrete test driver stubs ──────────────────────────────────────────


class StubNetworkDriver(NetworkDriver):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def configure_device(self, device):
        return True

    def upgrade_firmware(self, device):
        return True

    def reset_device(self, device):
        return True

    def verify_factory_state(self, device):
        return True


class StubServerDriver(ServerDriver):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def provision_server(self, device):
        return True

    def reset_server(self, device):
        return True


class StubApplianceDriver(ApplianceDriver):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def provision_device(self, device):
        return True

    def reset_device(self, device):
        return True


# ── Tests ────────────────────────────────────────────────────────────────


class TestDriverRegistry:
    def setup_method(self):
        DriverRegistry.clear()

    def teardown_method(self):
        DriverRegistry.clear()
        # Re-register builtins for other tests
        load_builtin_drivers()

    def test_register_and_lookup_network(self):
        DriverRegistry.register_network("test_", StubNetworkDriver)
        driver = DriverRegistry.get_network_driver("test_platform")
        assert driver is not None
        assert isinstance(driver, StubNetworkDriver)

    def test_register_and_lookup_server(self):
        DriverRegistry.register_server("test_", StubServerDriver)
        driver = DriverRegistry.get_server_driver("test_platform")
        assert driver is not None
        assert isinstance(driver, StubServerDriver)

    def test_register_and_lookup_appliance(self):
        DriverRegistry.register_appliance("test_", StubApplianceDriver)
        driver = DriverRegistry.get_appliance_driver("test_platform")
        assert driver is not None
        assert isinstance(driver, StubApplianceDriver)

    def test_unknown_platform_returns_none(self):
        assert DriverRegistry.get_network_driver("unknown_platform") is None
        assert DriverRegistry.get_server_driver("unknown_platform") is None
        assert DriverRegistry.get_appliance_driver("unknown_platform") is None

    def test_longest_prefix_match(self):
        """More specific prefix should win over a shorter one."""
        DriverRegistry.register_network("cisco_", StubNetworkDriver)

        class SpecificDriver(StubNetworkDriver):
            pass

        DriverRegistry.register_network("cisco_iosxe", SpecificDriver)

        # cisco_iosxe should match the more specific prefix
        driver = DriverRegistry.get_network_driver("cisco_iosxe_something")
        assert isinstance(driver, SpecificDriver)

        # cisco_ios should match the shorter prefix
        driver = DriverRegistry.get_network_driver("cisco_ios")
        assert isinstance(driver, StubNetworkDriver)

    def test_device_category(self):
        DriverRegistry.register_network("net_", StubNetworkDriver)
        DriverRegistry.register_server("srv_", StubServerDriver)
        DriverRegistry.register_appliance("app_", StubApplianceDriver)

        assert DriverRegistry.device_category("net_switch") == "network"
        assert DriverRegistry.device_category("srv_compute") == "server"
        assert DriverRegistry.device_category("app_ntp") == "appliance"
        assert DriverRegistry.device_category("unknown") is None

    def test_is_helpers(self):
        DriverRegistry.register_network("net_", StubNetworkDriver)
        DriverRegistry.register_server("srv_", StubServerDriver)
        DriverRegistry.register_appliance("app_", StubApplianceDriver)

        assert DriverRegistry.is_network("net_switch") is True
        assert DriverRegistry.is_network("srv_compute") is False
        assert DriverRegistry.is_server("srv_compute") is True
        assert DriverRegistry.is_appliance("app_ntp") is True

    def test_registered_platforms(self):
        DriverRegistry.register_network("cisco_", StubNetworkDriver)
        DriverRegistry.register_server("hpe_", StubServerDriver)
        DriverRegistry.register_appliance("meinberg_", StubApplianceDriver)

        platforms = DriverRegistry.registered_platforms()
        prefixes = [p[0] for p in platforms]
        assert "cisco_" in prefixes
        assert "hpe_" in prefixes
        assert "meinberg_" in prefixes

    def test_clear(self):
        DriverRegistry.register_network("test_", StubNetworkDriver)
        DriverRegistry.clear()
        assert DriverRegistry.get_network_driver("test_platform") is None

    def test_kwargs_passed_to_driver(self):
        DriverRegistry.register_server("test_", StubServerDriver)
        driver = DriverRegistry.get_server_driver("test_platform", foo="bar")
        assert driver.kwargs == {"foo": "bar"}


class TestBuiltinDriverRegistration:
    """Verify that built-in drivers register correctly."""

    def test_builtin_cisco_registered(self):
        load_builtin_drivers()
        assert DriverRegistry.is_network("cisco_ios")
        assert DriverRegistry.is_network("cisco_iosxe")
        assert DriverRegistry.is_network("cisco_asa")

    def test_builtin_hpe_registered(self):
        load_builtin_drivers()
        assert DriverRegistry.is_server("hpe_dl360_gen10")
        assert DriverRegistry.is_server("hpe_dl325_gen10")

    def test_builtin_meinberg_registered(self):
        load_builtin_drivers()
        assert DriverRegistry.is_appliance("meinberg_lantime")

    def test_device_categories_for_builtins(self):
        load_builtin_drivers()
        assert DriverRegistry.device_category("cisco_iosxe") == "network"
        assert DriverRegistry.device_category("hpe_dl380_gen10") == "server"
        assert DriverRegistry.device_category("meinberg_lantime") == "appliance"


class TestABCEnforcement:
    """Verify that ABCs cannot be instantiated directly."""

    def test_network_driver_is_abstract(self):
        with pytest.raises(TypeError):
            NetworkDriver()  # type: ignore[abstract]

    def test_server_driver_is_abstract(self):
        with pytest.raises(TypeError):
            ServerDriver()  # type: ignore[abstract]

    def test_appliance_driver_is_abstract(self):
        with pytest.raises(TypeError):
            ApplianceDriver()  # type: ignore[abstract]

    def test_discovery_driver_is_abstract(self):
        with pytest.raises(TypeError):
            DiscoveryDriver()  # type: ignore[abstract]
