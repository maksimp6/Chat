"""Transport boundaries must fail closed."""
import pytest

from alice_pro.printers.service import Transport, identify, cloud_reader
from alice_pro.printers.cloud_api import CloudApiNotConfigured


def test_no_cloud_discovery():
    with pytest.raises(ValueError):
        identify(Transport.CLOUD_API, host="192.168.31.12")


def test_no_implicit_cloud_fallback():
    with pytest.raises(ValueError):
        cloud_reader(Transport.LAN)


def test_cloud_disabled_by_default():
    with pytest.raises(CloudApiNotConfigured):
        cloud_reader(Transport.CLOUD_API).get_status("printer")


def test_lan_requires_explicit_host():
    with pytest.raises(ValueError):
        identify(Transport.LAN)
