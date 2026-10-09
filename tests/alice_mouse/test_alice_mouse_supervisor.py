import unittest
from alice_mouse_supervisor import Supervisor,Phase

class SupervisorTests(unittest.TestCase):
 def test_successful_switch(self):
  s=Supervisor()
  self.assertEqual(s.step("begin"),Phase.QUIESCE)
  self.assertEqual(s.step("legacy_stopped"),Phase.START_NEW)
  self.assertEqual(s.step("new_started"),Phase.VERIFY)
  self.assertEqual(s.step("healthy"),Phase.NEW)
  self.assertFalse(s.legacy_alive)
  self.assertTrue(s.new_alive)
 def test_failed_start_rolls_back(self):
  s=Supervisor()
  s.step("begin")
  s.step("legacy_stopped")
  self.assertEqual(s.step("failed"),Phase.ROLLBACK)
  self.assertEqual(s.step("legacy_restored"),Phase.LEGACY)
  self.assertTrue(s.legacy_alive)
 def test_timeout_before_stopping_legacy(self):
  s=Supervisor()
  s.step("begin")
  s.step("timeout")
  self.assertEqual(s.phase,Phase.ROLLBACK)
  self.assertFalse(s.new_alive)
 def test_no_two_mice(self):
  s=Supervisor()
  s.step("begin")
  with self.assertRaises(ValueError):
   s.step("new_started")
  self.assertTrue(s.legacy_alive)
  self.assertFalse(s.new_alive)
 def test_invalid_transition(self):
  with self.assertRaises(ValueError):
   Supervisor().step("healthy")
if __name__=="__main__":
 unittest.main()

[executed on device: localhost (bb12d9f4-83a6-41b5-9ede-8717bac12a0a)]
