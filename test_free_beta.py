"""HTTP integration tests. Storage is injected; no paid AI or network requests."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from flask import Flask
from werkzeug.test import Client
from werkzeug.wrappers import Response
from beta_server import BetaRouter, CloudHub, HubError, create_beta_app


class FakeHub:
    configured=True
    def __init__(self,fail_upload=False):self.calls=[];self.fail_upload=fail_upload
    def call(self,method,path,**kwargs):
        self.calls.append((method,path,kwargs))
        if self.fail_upload and method=='PUT':raise HubError('Upload unavailable')
        if path.endswith('/ready'):return {'ready':True}
        return {'status':'WAITING_FOR_AI'}


def fake_prepare(folder,**kwargs):
    stage=Path(folder)/'test-staged';stage.mkdir()
    (stage/'raw_data.json').write_text('{}')
    return {'directory':stage,'manifest':{'files':[{'name':'raw_data.json'}],'photos':[],'ai_calls':0,'chargeable':False}}


class FreeBetaHttpTests(unittest.TestCase):
    def test_unconfigured_storage_fails_before_scraping(self):
        app=create_beta_app({'TESTING':True,'CHECKNI_STORAGE_MODE':'cloudflare','CHECKNI_CLOUD_URL':'','CHECKNI_INGEST_TOKEN':''})
        with patch('beta_server.scrape') as scrape:
            response=app.test_client().post('/api/demo/analyze',json={'url':'https://auto.bazos.sk/inzerat/1/test.php'})
        self.assertEqual(response.status_code,503);scrape.assert_not_called()
        self.assertEqual(response.json['ai_api_calls'],0)
        self.assertFalse(app.test_client().get('/_beta/config').json['cloud_configured'])

    def test_url_intake_uploads_before_queue_without_model_calls(self):
        hub=FakeHub();app=create_beta_app({'TESTING':True},hub=hub)
        with patch('beta_server.scrape',side_effect=lambda url,work:work),patch('beta_server.prepare_assets',side_effect=fake_prepare),patch('requests.request',side_effect=AssertionError('Unexpected API call')):
            response=app.test_client().post('/api/demo/analyze',json={'url':'https://auto.bazos.sk/inzerat/1/test%20car.php'},buffered=True)
        text=response.get_data(as_text=True)
        self.assertIn('WAITING_FOR_AI',text);self.assertIn('data: [DONE]',text)
        create=next(c for c in hub.calls if c[0]=='POST' and c[1]=='/v1/jobs')
        self.assertTrue(create[2]['payload']['source_url'].endswith('test-car.php'))
        self.assertLess(next(i for i,c in enumerate(hub.calls) if c[0]=='PUT'),next(i for i,c in enumerate(hub.calls) if c[0]=='POST' and c[1].endswith('/ready')))

    def test_failed_upload_is_never_marked_waiting(self):
        hub=FakeHub(fail_upload=True);app=create_beta_app({'TESTING':True},hub=hub)
        with patch('beta_server.scrape',side_effect=lambda url,work:work),patch('beta_server.prepare_assets',side_effect=fake_prepare):
            response=app.test_client().post('/api/demo/analyze',json={'url':'https://auto.bazos.sk/inzerat/1/test.php'},buffered=True)
        self.assertNotIn('WAITING_FOR_AI',response.get_data(as_text=True))
        self.assertTrue(any(c[1].endswith('/fail') for c in hub.calls))
        self.assertFalse(any(c[0]=='POST' and c[1].endswith('/ready') for c in hub.calls))

    def test_manual_upload_uses_safe_filename_and_same_queue(self):
        hub=FakeHub();app=create_beta_app({'TESTING':True},hub=hub)
        def prepared(folder,**kw):
            self.assertTrue((folder/'images'/'001.upload').is_file());self.assertFalse((folder/'evil.jpg').exists())
            return fake_prepare(folder)
        with patch('beta_server.prepare_assets',side_effect=prepared):
            r=app.test_client().post('/api/demo/analyze-manual',data={'title':'Synthetic car','description':'A sufficiently detailed synthetic seller description.','images':(io.BytesIO(b'photo'),'../../evil.jpg')},buffered=True)
        self.assertIn('WAITING_FOR_AI',r.get_data(as_text=True))

    def test_invalid_url_does_not_allocate_storage(self):
        hub=FakeHub();app=create_beta_app({'TESTING':True},hub=hub)
        r=app.test_client().post('/api/demo/analyze',json={'url':'http://127.0.0.1/'})
        self.assertEqual(r.status_code,400);self.assertEqual(hub.calls,[])

    def test_router_blocks_old_paid_operations_but_preserves_old_reads(self):
        legacy=Flask('legacy');legacy.add_url_rule('/api/analyze/x','paid',lambda:'should not run',methods=['POST'])
        legacy.add_url_rule('/analysis/old-report','old',lambda:'old report')
        client=Client(BetaRouter(legacy,create_beta_app({'TESTING':True},hub=FakeHub())),Response)
        self.assertEqual(client.post('/api/analyze/x').status_code,403)
        self.assertEqual(client.post('/api/test-api-key').status_code,403)
        self.assertEqual(client.get('/analysis/old-report').text,'old report')
        self.assertIn('Bezplatná beta',client.get('/').text)
        self.assertFalse(client.get('/healthz').json['ai_api_calls_enabled'])

    def test_operator_requires_separate_credential_and_limits_routes(self):
        hub=FakeHub();client=create_beta_app({'TESTING':True},hub=hub).test_client()
        self.assertEqual(client.get('/_beta/operator/jobs').status_code,401)
        self.assertEqual(client.post('/_beta/operator/arbitrary',headers={'Authorization':'Bearer '+'a'*48},json={}).status_code,404)
        self.assertEqual(hub.calls,[])

    def test_cloud_configuration_requires_https_and_nonempty_token(self):
        self.assertFalse(CloudHub('http://cloud.test','x'*48).configured)
        self.assertFalse(CloudHub('https://user:pass@cloud.test','x'*48).configured)
        self.assertFalse(CloudHub('https://cloud.test','').configured)
        self.assertTrue(CloudHub('https://cloud.test','x'*48).configured)

if __name__=='__main__':unittest.main()
