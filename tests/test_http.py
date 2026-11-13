import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from report_agent.client import Client,task
from report_agent.common import load

class HTTPTests(unittest.TestCase):
    def test_identity_retry_and_record_cache(self):
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def send(self,data):
                b=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
            def do_GET(self):self.send({'status':'ready','model_path':'/tmp/test_base','adapter_path':'/tmp/test_adapter','bits':8,'features':['count_tokens'],'max_input_tokens':8192})
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path=='/count_tokens':self.send({'input_tokens':20,'max_input_tokens':8192});return
                calls.append(body);self.send({'text':'not json' if len(calls)==1 else '{"paragraphs":["已核对内容"],"source_refs":["s:1"]}','limit_reached':False})
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            config={'base_url':f'http://127.0.0.1:{server.server_port}','expected_model_path':'/tmp/test_base','expected_adapter_path':'/tmp/test_adapter','expected_bits':8}
            c=Client(config)
            with self.assertRaises(ValueError):Client({**config,'expected_adapter_path':'/tmp/wrong'})
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'record.json';messages=[{'role':'user','content':'填本节'}]
                def validate(a):
                    if not a.get('paragraphs'):raise ValueError('缺少paragraphs')
                a=task(c,messages,p,validate,{'case_id':'a','group_id':'a'});self.assertEqual(len(calls),2);self.assertFalse(load(p)['review']['approved'])
                self.assertEqual(task(c,messages,p,validate,{}),a);self.assertEqual(len(calls),2)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
