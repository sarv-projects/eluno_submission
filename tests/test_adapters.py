# 429/503 retry handling, tiny local http server so nothing external needed

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pos.adapters import HttpClient

class RetryHandler(BaseHTTPRequestHandler):
    counts={}
    def do_POST(self):
        type(self).counts[self.path]=type(self).counts.get(self.path,0)+1
        n=type(self).counts[self.path]
        if n==1:
            code=429 if self.path=="/shopify" else 503
            body={"error":"temporary"}
            self.send_response(code)
            if code==429: self.send_header("Retry-After","0")
        else:
            code=200; body={"ok":True}
            self.send_response(code)
        raw=json.dumps(body).encode()
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers(); self.wfile.write(raw)
    def log_message(self,*args): pass

class AdapterRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        RetryHandler.counts={}
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),RetryHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
        cls.client=HttpClient("http://127.0.0.1:{}".format(cls.server.server_address[1]),attempts=3,sleeper=lambda _:None)
    @classmethod
    def tearDownClass(cls): cls.server.shutdown(); cls.server.server_close()
    def test_shopify_429_retries_after_header(self):
        self.assertTrue(self.client.request("POST","/shopify",{} )["ok"])
        self.assertEqual(RetryHandler.counts["/shopify"],2)
    def test_terminal_503_retries_when_explicitly_enabled(self):
        self.assertTrue(self.client.request("POST","/terminal",{},retry_terminal_503=True)["ok"])
        self.assertEqual(RetryHandler.counts["/terminal"],2)
if __name__=="__main__": unittest.main()
