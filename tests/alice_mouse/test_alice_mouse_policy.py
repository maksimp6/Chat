import unittest
from alice_mouse_policy import authorize,parse_mouse_command
A=frozenset({"org.example.safe"})
class PolicyTests(unittest.TestCase):
 def test_viewer_denied_mouse(self):
  for action in ("move","click","scroll"):
   self.assertFalse(authorize("viewer",action,foreground="org.example.safe",allowed_apps=A).allowed)
 def test_controller_mouse(self):
  for action in ("move","click","scroll"):
   self.assertTrue(authorize("controller",action,foreground="org.example.safe",allowed_apps=A).allowed)
 def test_no_keyboard_or_shell(self):
  for role in ("viewer","controller","admin"):
   for action in ("text","keyevent","shell","paste","back","home"):
    self.assertFalse(authorize(role,action,foreground="org.example.safe",allowed_apps=A).allowed)
 def test_foreground(self):
  self.assertFalse(authorize("controller","click",foreground="org.other",allowed_apps=A).allowed)
 def test_sensitive(self):
  self.assertFalse(authorize("controller","click",foreground="org.example.safe",allowed_apps=A,sensitive=True).allowed)
  self.assertTrue(authorize("controller","click",foreground="org.example.safe",allowed_apps=A,sensitive=True,confirmed=True).allowed)
 def test_unknown_role(self):
  self.assertFalse(authorize("root","click",foreground="org.example.safe",allowed_apps=A).allowed)
 def test_parse(self):
  self.assertEqual(parse_mouse_command({"action":"click","x":2,"y":-4}),("click",2,-4))
  for bad in ({"action":"text","x":0,"y":0},{"action":"click","x":True,"y":0},{"action":"click","x":501,"y":0},{"action":"scroll","x":1,"y":1},{"action":"click","x":0,"y":0,"token":"x"},"click",{}):
   self.assertIsNone(parse_mouse_command(bad))
if __name__=="__main__": unittest.main()

[executed on device: localhost (bb12d9f4-83a6-41b5-9ede-8717bac12a0a)]
