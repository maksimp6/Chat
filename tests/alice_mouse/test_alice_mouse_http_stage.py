import json
import socket
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from alice_mouse_http_stage import MouseHandler
from alice_mouse_bridge import MouseBridge

class StageTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1",0), MouseHandler)
        MouseHandler.bridge = MouseBridge("/nonexistent/alice.sock",{"org.example.safe"})
        MouseHandler.foreground = "org.example.safe"
        MouseHandler.token_roles = {"viewer-test-key": "viewer", "controller-test-key": "controller"}
        self.thread = threading.Thread(target=self.server.serve_forever,kwargs={"poll_interval":0.01},daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:"+str(self.server.server_address[1])
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
    def request(self, role, body):
        raw = json.dumps(body).encode()
        req = urllib.request.Request(self.base+"/mouse",raw,{"Content-Type":"application/json","Authorization":"Bearer "+role+"-test-key"},method="POST")
        try:
            with urllib.request.urlopen(req,timeout=2) as res:
                return res.status,json.load(res)
        except urllib.error.HTTPError as error:
            try:
                return error.code,json.load(error)
            finally:
                error.close()
    def test_viewer_denied(self):
        status,data=self.request("viewer",{"action":"click","x":0,"y":0})
        self.assertEqual(status,403)
        self.assertEqual(data["error"],"role_denied")
    def test_controller_denied_when_device_missing(self):
        status,data=self.request("controller",{"action":"move","x":1,"y":1})
        self.assertEqual(status,403)
        self.assertEqual(data["error"],"bridge_unavailable")
    def test_keyboard_denied(self):
        status,data=self.request("controller",{"action":"text","x":1,"y":1})
        self.assertEqual(status,403)
        self.assertEqual(data["error"],"invalid_command")
    def test_untrusted_role_header(self):
        status,data=self.request("admin",{"action":"click","x":0,"y":0})
        self.assertEqual(status,401)
    def test_health(self):
        with urllib.request.urlopen(self.base+"/health",timeout=2) as res:
            self.assertEqual(res.status,200)
if __name__=="__main__":
    unittest.main()
