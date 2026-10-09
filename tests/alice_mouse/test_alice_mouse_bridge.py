import os
import socket
import tempfile
import threading
import unittest
from alice_mouse_bridge import MouseBridge

APP = "org.example.safe"
class BridgeTests(unittest.TestCase):
    def test_deny_before_socket(self):
        bridge = MouseBridge("/path/that/does/not/exist", {APP})
        for role, action, foreground in (
            ("viewer", "click", APP),
            ("controller", "click", "org.other"),
            ("controller", "text", APP),
        ):
            self.assertFalse(bridge.handle(role, {"action":action,"x":0,"y":0},foreground)["ok"])

    def test_unavailable_fail_closed(self):
        bridge = MouseBridge("/path/that/does/not/exist", {APP})
        self.assertEqual(bridge.handle("controller",{"action":"move","x":1,"y":2},APP)["error"],"bridge_unavailable")

    def test_synthetic_socket_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,"mouse.sock")
            server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            server.bind(path)
            server.listen(1)
            received=[]
            def worker():
                conn,_=server.accept()
                with conn:
                    received.append(conn.recv(80))
                    conn.sendall(b"OK\n")
            thread=threading.Thread(target=worker)
            thread.start()
            try:
                bridge=MouseBridge(path,{APP})
                result=bridge.handle("controller",{"action":"move","x":10,"y":-8},APP)
                self.assertTrue(result["ok"])
                self.assertEqual(received,[b"move 10 -8\n"])
            finally:
                thread.join(timeout=2)
                server.close()

if __name__=="__main__":
    unittest.main()

[executed on device: localhost (bb12d9f4-83a6-41b5-9ede-8717bac12a0a)]
