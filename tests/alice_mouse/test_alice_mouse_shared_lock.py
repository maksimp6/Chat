import fcntl
import multiprocessing as mp
import os
import tempfile
import unittest

def contender(path,queue):
    with open(path,"a+b") as f:
        try:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            queue.put("ACQUIRED")
        except BlockingIOError:
            queue.put("DENIED")

class SharedLockTests(unittest.TestCase):
    def test_competing_process_denied_then_recovers(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,"shared.lock")
            with open(path,"a+b") as held:
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                q=mp.Queue()
                p=mp.Process(target=contender,args=(path,q))
                p.start()
                self.assertEqual(q.get(timeout=3),"DENIED")
                p.join(timeout=3)
                self.assertEqual(p.exitcode,0)
                fcntl.flock(held,fcntl.LOCK_UN)
            q=mp.Queue()
            p=mp.Process(target=contender,args=(path,q))
            p.start()
            self.assertEqual(q.get(timeout=3),"ACQUIRED")
            p.join(timeout=3)
            self.assertEqual(p.exitcode,0)

if __name__=="__main__":
    unittest.main()

[executed on device: localhost (bb12d9f4-83a6-41b5-9ede-8717bac12a0a)]
