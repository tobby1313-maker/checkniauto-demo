"""Deterministic beta regression tests; no network or model-provider calls."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from scrapper_demo.beta_prepare import PreparationError, normalize_url, prepare_assets


class BetaPreparationTests(unittest.TestCase):
    def folder(self, root, count):
        folder=Path(root);(folder/'images').mkdir()
        (folder/'raw_data.json').write_text(json.dumps({'title':'Synthetic Jeep test','description':'The seller claims service history without supplying any invoices.','photos_count':count,'parameters':{},'url':'https://auto.bazos.sk/inzerat/123/test.php'}),encoding='utf-8')
        return folder

    def test_normalizes_only_a_valid_supported_listing(self):
        url='https://auto.bazos.sk/inzerat/195434376/jeep-mjet%20140.php'
        self.assertEqual(normalize_url(url),url.replace('%20','-'))
        for bad in ['http://localhost/inzerat/1/test.php','https://auto.bazos.sk.evil.test/inzerat/1/test.php','https://user:secret@auto.bazos.sk/inzerat/1/test.php','https://auto.bazos.sk/inzeraty/jeep/','https://auto.bazos.sk:8000/inzerat/1/test.php']:
            with self.subTest(bad=bad),self.assertRaises(PreparationError): normalize_url(bad)

    def test_repetitive_gallery_keeps_all_photos_and_marks_none_inspected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=self.folder(tmp,20)
            for i in range(1,21):Image.new('RGB',(120,80),(90,100,120)).save(folder/'images'/f'{i:02d}_car.jpg')
            originals={p.name:p.read_bytes() for p in (folder/'images').iterdir()}
            with patch('requests.request',side_effect=AssertionError('Network forbidden')),patch('scrapper_demo.beta_prepare.run_child',side_effect=AssertionError('Child forbidden')):
                result=prepare_assets(folder,market=False)
            m=result['manifest']
            self.assertEqual(len(m['photos']),20);self.assertEqual(len(m['sheets']),5)
            self.assertEqual({p['id'] for p in m['photos']},{x for s in m['sheets'] for x in s['photo_ids']})
            self.assertTrue(all(p['inspection']=='not_inspected' for p in m['photos']))
            self.assertEqual(m['ai_calls'],0);self.assertFalse(m['chargeable'])
            self.assertEqual(originals,{p.name:p.read_bytes() for p in (folder/'images').iterdir()})
            for f in m['files']:self.assertEqual(f['sha256'],hashlib.sha256((result['directory']/f['name']).read_bytes()).hexdigest())

    def test_missing_or_unreadable_photos_do_not_shift_original_photo_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=self.folder(tmp,4)
            (folder/'images'/'01_bad.jpg').write_bytes(b'not an image')
            Image.new('RGB',(120,80)).save(folder/'images'/'03_good.jpg')
            result=prepare_assets(folder,market=False);m=result['manifest']
            self.assertEqual([p['id'] for p in m['photos']],['p001','p002','p003','p004'])
            self.assertEqual([p['status'] for p in m['photos']],['unreadable','download_failed','available','download_failed'])
            self.assertEqual(m['sheets'][0]['photo_ids'],['p003'])

    def test_oversize_gallery_fails_instead_of_silent_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(PreparationError,'60'):
                prepare_assets(self.folder(tmp,61),market=False)

    def test_no_photos_and_market_failure_do_not_prevent_manual_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=self.folder(tmp,0)
            with patch('scrapper_demo.beta_prepare.run_child',side_effect=PreparationError('offline')):
                result=prepare_assets(folder)
            self.assertEqual(result['manifest']['photos'],[])
            self.assertEqual(result['manifest']['market']['status'],'unavailable')
            self.assertFalse(result['manifest']['market']['verified_comparison'])

    def test_resize_records_that_download_is_not_an_original_camera_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=self.folder(tmp,1);Image.new('RGB',(2400,1600)).save(folder/'images'/'01_car.jpg')
            p=prepare_assets(folder,market=False)['manifest']['photos'][0]
            self.assertEqual(p['source_dimensions'],[2400,1600]);self.assertEqual(max(p['stored_dimensions']),1600)
            self.assertEqual(p['stored_variant'],'jpeg_max_1600px')

if __name__=='__main__':unittest.main()
