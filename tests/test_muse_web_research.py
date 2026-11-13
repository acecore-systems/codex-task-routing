import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'plugins/codex-task-routing/scripts'))
import muse_web_research as web


class ResearchTests(unittest.TestCase):
 def setUp(self):
  prompt='公開情報を調べる'; self.digest=hashlib.sha256(prompt.encode()).hexdigest()
  self.facts=dict(schema_version=1,actor='root',explicit_opt_in=True,host_authorized=True,target_authorized=True,materials_authorized=True,cost_authorized=True,data_class='public',purpose='public_web_research',prompt=prompt,prompt_sha256=self.digest,status={'state':'not_started','inputSha256':self.digest})
  self.output=[{'type':'web_search_call','status':'completed'},{'type':'output_text','text':'source','annotations':[{'type':'url_citation','url':'https://example.com/','title':'source','start_index':0,'end_index':6}]}]
 def test_permissions_and_fallback(self):
  self.assertTrue(web.prepare(self.facts)['ready'])
  for key in ('explicit_opt_in','host_authorized','target_authorized','materials_authorized','cost_authorized'):
   self.assertEqual(web.prepare({**self.facts,key:False})['next_action'],'use_existing_policy')
  for state in ('intent','unverified','completed'):
   result=web.prepare({**self.facts,'status':{'state':state,'inputSha256':self.digest}})
   self.assertFalse(result['ready']);self.assertEqual(result['next_action'],'review_existing_result' if state=='completed' else 'reconcile_existing_request')
  self.assertEqual(web.prepare({**self.facts,'status':None})['next_action'],'check_request_status')
  self.assertFalse(web.prepare({**self.facts,'purpose':'other'})['ready'])
 def test_grounding_and_citations(self):
  facts=dict(schema_version=1,prompt_sha256=self.digest,result=dict(state='completed',inputSha256=self.digest,model='muse-ember-1.0',effort=None,inputTokens=2,outputTokens=3,output=self.output))
  self.assertTrue(web.verify(facts)['verified'])
  with self.assertRaises(ValueError):web.verify({**facts,'result':{**facts['result'],'effort':'high'}})
  japanese={'type':'output_text','text':'日本語の出典','annotations':[{'type':'url_citation','url':'https://example.com/','title':'出典','start_index':3,'end_index':6}]}
  self.assertTrue(web.verify({**facts,'result':{**facts['result'],'output':[self.output[0],japanese]}})['verified'])
  with self.assertRaises(ValueError):web.verify({**facts,'result':{**facts['result'],'output':[self.output[0]]*4+[self.output[1]]}})
  for output in (self.output[1:],self.output[:1],[self.output[0],{**self.output[1],'annotations':[]}],[self.output[0],{**self.output[1],'annotations':[{**self.output[1]['annotations'][0],'url':'file:///secret'}]}]):
   with self.subTest(output=output),self.assertRaises(ValueError):web.verify({**facts,'result':{**facts['result'],'output':output}})

 def test_invalid_citation_urls(self):
  invalid=('https://example.com:invalid/', 'https://example.com:65536/',
           'https://example.com:0/', 'https://example.com:-1/',
           'https://exam\nple.com/', 'https://example.com/\tpath',
           'https://exa mple.com/', ' https://example.com/',
           'https://example.com/\x00', 'https://example.com/\x7f',
           'https://example.com/\\path', 'https://@example.com/',
           'https://:@example.com/')
  for url in invalid:
   output=[self.output[0],{**self.output[1],'annotations':[{**self.output[1]['annotations'][0],'url':url}]}]
   facts=dict(schema_version=1,prompt_sha256=self.digest,result=dict(state='completed',inputSha256=self.digest,model='muse-ember-1.0',effort='max',inputTokens=None,outputTokens=None,output=output))
   with self.subTest(url=url),self.assertRaises(ValueError):web.verify(facts)
 def test_valid_citation_urls(self):
  for url in ('https://example.com/', 'http://example.com:8080/source',
              'https://example.com:443/日本語?q=public%20source',
              'https://[2606:4700:4700::1111]:443/source'):
   output=[self.output[0],{**self.output[1],'annotations':[{**self.output[1]['annotations'][0],'url':url}]}]
   facts=dict(schema_version=1,prompt_sha256=self.digest,result=dict(state='completed',inputSha256=self.digest,model='muse-ember-1.0',effort='max',inputTokens=None,outputTokens=None,output=output))
   with self.subTest(url=url):self.assertTrue(web.verify(facts)['verified'])

if __name__=='__main__':unittest.main()
