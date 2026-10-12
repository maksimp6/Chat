"""Contract tests: LAN and cloud API are isolated."""
import pytest
from alice_pro.printers.gateway import (
    Operation, PrinterGateway, PrinterRequest, Transport, TransportUnavailable,
)


class Stub:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        return {"transport": self.name}


def test_explicit_routing():
    lan, cloud = Stub("lan"), Stub("cloud_api")
    gateway = PrinterGateway(lan=lan, cloud_api=cloud)
    req = PrinterRequest(Operation.STATUS)
    assert gateway.execute(Transport.LAN, req) == {"transport": "lan"}
    assert len(lan.calls) == 1 and not cloud.calls
    assert gateway.execute(Transport.CLOUD_API, req) == {"transport": "cloud_api"}
    assert len(cloud.calls) == 1 and len(lan.calls) == 1


def test_no_fallback_to_cloud():
    cloud = Stub("cloud_api")
    gateway = PrinterGateway(cloud_api=cloud)
    with pytest.raises(TransportUnavailable):
        gateway.execute(Transport.LAN, PrinterRequest(Operation.STATUS))
    assert not cloud.calls


def test_transport_required():
    gateway = PrinterGateway(lan=Stub("lan"))
    with pytest.raises(TypeError):
        gateway.execute("lan", PrinterRequest(Operation.DISCOVER))
