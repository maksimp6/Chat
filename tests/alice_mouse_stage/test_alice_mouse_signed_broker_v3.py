import base64,hashlib,hmac,json,os,secrets,socket,tempfile,threading,time,unittest
from pathlib import Path
from alice_mouse_signed_broker_v3 import SignedBroker,serve

class TestBroker(unittest.TestCase):
 def setUp(self):
  self.now=10_000_000_000;self.key=secrets.token_bytes(32);self.calls=[]
  self.b=SignedBroker(self.key,os.getuid(),clock=lambda:self.now,forward=lambda grant:self.calls.append(grant) or True)
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'broker.sock'
  self.stop=threading.Event();self.ready=threading.Event()
  self.worker=threading.Thread(target=serve,args=(self.path,self.b,self.stop,self.ready))
  self.worker.start();self.assertTrue(self.ready.wait(2))
 def tearDown(self):
  self.stop.set();self.worker.join(2);self.assertFalse(self.worker.is_alive());self.temp.cleanup()
 def sign(self,**kwargs):
  grant=dict(role='controller',action='move',seq=1,nonce=secrets.token_hex(16),until=self.now+200_000_000,epoch=self.b.epoch,x=10,y=-5,button=1)
  grant.update(kwargs);raw=json.dumps(grant,sort_keys=True).encode()
  return json.dumps(dict(grant=base64.b64encode(raw).decode(),sig=hmac.digest(self.key,raw,'sha256').hex())).encode()
 def send(self,packet):
  with socket.socket(socket.AF_UNIX) as s:
   s.settimeout(2);s.connect(str(self.path));s.sendall(packet);return s.recv(64)
 def test_signed_coordinates(self):
  self.assertEqual(self.send(self.sign()),b'OK\n');self.assertEqual((self.calls[0]['x'],self.calls[0]['y']),(10,-5))
 def test_viewer_denied(self):self.assertEqual(self.send(self.sign(role='viewer')),b'DENIED\n')
 def test_wrong_uid_denied(self):self.assertEqual(self.b.handle(os.getuid()+1,self.sign()),b'DENIED\n')
 def test_replay_denied(self):
  p=self.sign();self.assertEqual(self.send(p),b'OK\n');self.assertEqual(self.send(p),b'DENIED\n')
 def test_coordinates_bounds(self):
  for x in (501,-501,True):self.assertEqual(self.send(self.sign(x=x)),b'DENIED\n')
 def test_button_denied(self):self.assertEqual(self.send(self.sign(button=3)),b'DENIED\n')
 def test_expired_denied(self):
  p=self.sign();self.now+=301_000_000;self.assertEqual(self.send(p),b'DENIED\n')
 def test_key_rotation_denies_old_grant(self):
  p=self.sign();self.b.rotate(secrets.token_bytes(32));self.assertEqual(self.send(p),b'DENIED\n')
 def test_bounded_nonce_store(self):
  for _ in range(1024):self.assertEqual(self.b.handle(os.getuid(),self.sign()),b'OK\n')
  self.assertEqual(self.b.handle(os.getuid(),self.sign()),b'DENIED\n');self.assertEqual(len(self.b.seen),1024)
 def test_concurrent_replay_single_accept(self):
  import concurrent.futures
  packet=self.sign()
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
   results=list(pool.map(self.send,[packet]*8))
  self.assertEqual(results.count(b'OK\n'),1)
  self.assertEqual(results.count(b'DENIED\n'),7)
 def test_invalid_signature(self):
  p=json.loads(self.sign());p['sig']='00'*32;self.assertEqual(self.send(json.dumps(p).encode()),b'DENIED\n')
if __name__=='__main__':unittest.main()
