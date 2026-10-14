"""IMEI + report sharing tests (no external network or paid providers)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from backend.src import report_sharing as sharing


class SharingUnitTests(unittest.TestCase):
    def test_imei_checksum_only(self):
        good = sharing.check_imei('490154203237518')
        self.assertTrue(good['checksum_valid'])
        self.assertEqual(good['masked_imei'], '•••••••••••7518')
        self.assertEqual(good['blacklist_status'], 'not_checked')
        self.assertEqual(good['model_status'], 'not_checked')
        self.assertFalse(sharing.check_imei('490154203237519')['checksum_valid'])
        self.assertFalse(sharing.check_imei('12345')['format_valid'])
        self.assertFalse(sharing.check_imei('49015420323751x')['format_valid'])

    def test_minimal_snapshot_and_html_escaping(self):
        sample = {'device_name': '<img src=x onerror=alert(1)>', 'market_value': 'RM1,000', 'imei': {'masked_imei':'•••••••••••7518', 'status':'checksum_valid'}, 'internal_profile':'sample_a_minus'}
        clean=sharing.safe_snapshot(sample)
        self.assertNotIn('490154203237518', json.dumps(clean))
        page=sharing.report_html(clean)
        self.assertNotIn('<img src=x', page)
        self.assertIn('&lt;img', page)
        self.assertIn('blacklist not checked', page)
        pdf=sharing.report_pdf(clean)
        self.assertTrue(pdf.startswith(b'%PDF-1.4'))
        self.assertIn(b'%%EOF', pdf)

    def test_snapshot_statuses_and_pdf_text_integrity(self):
        common={'report_id':'RVA-S24U-A91-1014','device_name':'Samsung Galaxy S24 Ultra 256GB','storage':'256 GB',
                'market_value':'RM2,250–RM2,600','exterior_grade':'A','evidence_date':'2026-09-08',
                'internal_profile':'sample_a_minus'}
        valid=sharing.safe_snapshot(dict(common,imei={'status':'checksum_valid','masked_imei':'•••••••••••7518'}))
        skipped=sharing.safe_snapshot(dict(common,imei={'status':'skipped'}))
        unchecked=sharing.safe_snapshot(dict(common,imei={'status':'not_checked'}))
        self.assertEqual(valid['imei']['masked_imei'],'•••••••••••7518')
        self.assertEqual(skipped['imei']['status'],'skipped')
        self.assertEqual(unchecked['imei']['status'],'not_checked')
        self.assertNotIn('490154203237518',json.dumps(valid))
        pdf=sharing.report_pdf(valid)
        text='\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(pdf)).pages)
        self.assertIn('RM2,250–RM2,600',text)
        self.assertIn('RVA-S24U-A91-1014',text)
        self.assertIn('Samsung Galaxy S24 Ultra 256GB',text)
        self.assertNotIn('490154203237518',text)
        self.assertEqual(len(PdfReader(BytesIO(pdf)).pages),2)

    def test_report_store_token_and_expiry(self):
        with tempfile.TemporaryDirectory() as temp:
            original=sharing.REPORT_DB
            sharing.REPORT_DB=Path(temp)/'reports.sqlite3'
            try:
                token,expires,_=sharing.save_report({'device_name':'Samsung Galaxy S24 Ultra','market_value':'RM2,250-RM2,600'})
                self.assertEqual(len(token),32)
                self.assertIsNotNone(sharing.load_report(token))
                self.assertIsNone(sharing.load_report('RVA-LIVE-1234'))
                self.assertGreater(expires, int(time.time()))
            finally:
                sharing.REPORT_DB=original


class SharingHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socket
        cls.temp=tempfile.TemporaryDirectory()
        sock=socket.socket()
        sock.bind(('127.0.0.1',0));cls.port=sock.getsockname()[1];sock.close()
        cls.origin=f'http://127.0.0.1:{cls.port}'
        env=os.environ.copy()
        env.update(PORT=str(cls.port), REVALUE_PUBLIC_BASE_URL=cls.origin,REVALUE_REPORT_DB=str(Path(cls.temp.name)/'db.sqlite3'))
        cls.proc=subprocess.Popen([sys.executable,'-m','backend.src.server'],cwd=str(ROOT),env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for i in range(45):
            if cls.proc.poll() is not None:
                raise RuntimeError('server exited: '+cls.proc.stderr.read().decode()[:1000])
            try:
                with urlopen(cls.origin+'/health', timeout=1) as response:
                    if response.status==200:return
            except Exception:time.sleep(.1)
        raise RuntimeError('server did not start')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:cls.proc.wait(timeout=4)
        except subprocess.TimeoutExpired:cls.proc.kill()
        cls.temp.cleanup()

    @classmethod
    def post(cls,path,body):
        data=json.dumps(body).encode()
        req=Request(cls.origin+path,data=data,method='POST',headers={'Content-Type':'application/json'})
        return urlopen(req, timeout=15)

    def test_end_to_end_reports(self):
        with self.post('/imei/check',{'imei':'490154203237518'}) as r:
            item=json.load(r)
        self.assertTrue(item['checksum_valid'])
        self.assertEqual(item['blacklist_status'],'not_checked')
        payload={'report_id':'RVA-LIVE-1234','device_name':'Samsung Galaxy S24 Ultra','market_value':'RM2,250–RM2,600','exterior_grade':'A',
                 'imei': {'status':'checksum_valid','masked_imei':item['masked_imei']},'internal_profile':'sample_a_minus'}
        with self.post('/reports/export.pdf',payload) as r:
            self.assertEqual(r.status,200)
            self.assertEqual(r.headers.get_content_type(),'application/pdf')
            self.assertTrue(r.read().startswith(b'%PDF'))
        with self.post('/reports',payload) as r:
            self.assertEqual(r.status,201)
            refs=json.load(r)
        with urlopen(refs['share_url']) as r:
            html=r.read().decode()
            self.assertIn('Samsung Galaxy S24 Ultra',html)
            self.assertIn('noindex',r.headers.get('X-Robots-Tag',''))
            self.assertNotIn('490154203237518',html)
        with urlopen(refs['pdf_url']) as r:self.assertTrue(r.read().startswith(b'%PDF'))
        try:
            with urlopen(refs['qr_url']) as r:
                self.assertIn(b'<svg',r.read(400))
        except HTTPError as err:
            # Optional dependency absent: explicit 503 rather than fake pattern.
            self.assertEqual(err.code,503)


if __name__ == '__main__':
    unittest.main(verbosity=2)
