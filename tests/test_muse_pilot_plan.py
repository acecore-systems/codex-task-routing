import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/codex-task-routing/scripts'))
import muse_pilot_plan as pilot

class MusePilotTests(unittest.TestCase):
 def setUp(self):
  self.prompt='公開資料の受入条件を要約';self.digest=hashlib.sha256(self.prompt.encode()).hexdigest()
  self.status=dict(state='not_started',inputSha256=None,model=None,effort=None,inputTokens=None,outputTokens=None,seconds=None,text=None)
  self.facts=dict(schema_version=1,actor='root',explicit_opt_in=True,host_authorized=True,target_authorized=True,materials_authorized=True,data_class='public_or_synthetic',prompt=self.prompt,prompt_sha256=self.digest,status=self.status)
 def test_prepare_requires_all_permissions_and_fixed_status(self):
  result=pilot.prepare(self.facts);self.assertTrue(result['ready']);self.assertFalse(result['sent']);self.assertEqual(result['arguments']['promptSha256'],self.digest)
  for key in ('explicit_opt_in','host_authorized','target_authorized','materials_authorized'):
   result=pilot.prepare({**self.facts,key:False});self.assertFalse(result['ready']);self.assertNotIn('arguments',result)
  for state in ('intent','unverified','completed','unknown'):
   self.assertFalse(pilot.prepare({**self.facts,'status':{**self.status,'state':state}})['ready'])
  self.assertFalse(pilot.prepare({**self.facts,'status':None})['ready'])
 def test_scope_hash_and_bytes_fail_closed(self):
  for changes in ({'actor':'child'},{'data_class':'private'},{'prompt_sha256':'0'*64},{'host_authorized':1},{'prompt':'あ'*1366}):
   with self.subTest(changes=changes),self.assertRaises(ValueError):pilot.prepare({**self.facts,**changes})
 def test_verify_does_not_accept_unverified_model_or_hash(self):
  result=dict(state='completed',inputSha256=self.digest,model='muse-ember-1.0',effort=None,inputTokens=2,outputTokens=3,seconds=1.0,text='確認項目')
  verified=pilot.verify(dict(schema_version=1,prompt_sha256=self.digest,result=result));self.assertTrue(verified['verified']);self.assertFalse(verified['accepted']);self.assertTrue(verified['parent_acceptance_required'])
  for change in ({'state':'unverified'},{'model':'other'},{'inputSha256':'0'*64},{'text':''},{'outputTokens':True}):
   with self.subTest(change=change),self.assertRaises(ValueError):pilot.verify(dict(schema_version=1,prompt_sha256=self.digest,result={**result,**change}))

if __name__=='__main__':unittest.main()
