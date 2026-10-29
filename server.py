import json
import os
import sys
from http.server import HTTPServer, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "starter-kit", "mock-apis"))

import mock_server as mock

mock.FLAKE = False

from pos.adapters import HttpClient, ShopifyAdapter, StoreToolAdapter, TerminalAdapter
from pos.service import PosService, DomainError, event_date
from pos.storage import Store

PORT = int(os.environ.get("PORT", "8000"))
DB_PATH = os.environ.get("DB", os.path.join(ROOT, "pos_web.sqlite3"))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(ROOT, "starter-kit", "data"))
BASE_URL = "http://127.0.0.1:{}".format(PORT)

http = HttpClient(BASE_URL, sleeper=lambda _: None)
shopify = ShopifyAdapter(http)
store_tool = StoreToolAdapter(http)
terminal = TerminalAdapter(http)
store = Store(DB_PATH)
service = PosService(shopify, store_tool, terminal, store, DATA_DIR)


class WebHandler(mock.H):
    def _send_json(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _serve_file(self, rel_path, content_type):
        path = os.path.join(ROOT, "web", rel_path)
        if not os.path.isfile(path):
            self._send_json(404, {"error": "not found"})
            return
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._serve_file("index.html", "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            ctype = "text/plain"
            if rel.endswith(".css"):
                ctype = "text/css; charset=utf-8"
            elif rel.endswith(".js"):
                ctype = "application/javascript; charset=utf-8"
            elif rel.endswith(".html"):
                ctype = "text/html; charset=utf-8"
            self._serve_file(rel, ctype)
            return
        if path.startswith("/api/"):
            self._handle_api_get(path, query)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            body = self._read_body()
            self._handle_api_post(path, body)
            return
        super().do_POST()

    def _handle_api_get(self, path, query):
        def q(name, default=None):
            vals = query.get(name)
            return vals[0] if vals else default

        try:
            if path == "/api/health":
                self._send_json(200, {"ok": True})
                return
            if path == "/api/locations":
                self._send_json(200, {"locations": shopify.locations()})
                return
            if path == "/api/products":
                ptype = q("product_type", "Frame")
                self._send_json(200, {"products": shopify.products(ptype)})
                return
            if path == "/api/inventory":
                item_ids = [int(x) for x in (q("item_ids", "") or "").split(",") if x.strip()]
                location_id = int(q("location_id"))
                self._send_json(200, {"inventory_levels": shopify.inventory(item_ids, location_id)})
                return
            if path == "/api/staff":
                self._send_json(200, {"staff": store_tool.staff(q("store", ""))})
                return
            if path == "/api/prescriptions":
                self._send_json(200, {"prescriptions": store_tool.prescriptions(q("phone", ""))})
                return
            if path == "/api/customers":
                self._send_json(200, {"customers": shopify.customers_by_phone(q("phone", ""))})
                return
            if path == "/api/orders":
                location_id = q("location_id")
                if location_id is not None:
                    location_id = int(location_id)
                limit = int(q("limit", "250"))
                self._send_json(200, {"orders": shopify.orders(location_id=location_id, limit=limit)})
                return
            if path == "/api/order":
                self._send_json(200, {"order": shopify.order(int(q("id")))})
                return
            if path == "/api/report":
                report = service.day_report(q("store"), q("date"))
                self._send_json(200, report)
                return
            self._send_json(404, {"error": "unknown endpoint"})
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})

    def _handle_api_post(self, path, body):
        try:
            if path == "/api/quote":
                pairs = body.get("pairs", [])
                result = service.quote(
                    body["phone"], body["store"], body["staff"], pairs,
                    channel=body.get("channel", "walk_in"),
                    payment_method=body.get("payment", "upi"),
                    sale_date=body.get("date"),
                    manager_percent=int(body.get("manager_percent", 0) or 0),
                    reason_code=body.get("reason_code"),
                    approver_id=body.get("approver_id"),
                )
                self._send_json(200, result)
                return
            if path == "/api/checkout":
                pairs = body.get("pairs", [])
                result = service.checkout(
                    body["phone"], body["store"], body["staff"], pairs,
                    channel=body.get("channel", "walk_in"),
                    payment_method=body.get("payment", "upi"),
                    sale_date=body.get("date"),
                    manager_percent=int(body.get("manager_percent", 0) or 0),
                    reason_code=body.get("reason_code"),
                    approver_id=body.get("approver_id"),
                    checkout_key=body.get("checkout_key"),
                )
                self._send_json(200, result)
                return
            if path == "/api/refund":
                result = service.refund(
                    body["invoice"], body["store"], body["reason"], body["reason_code"],
                    refund_key=body.get("refund_key"),
                )
                self._send_json(200, result)
                return
            self._send_json(404, {"error": "unknown endpoint"})
        except (DomainError, ValueError, RuntimeError, KeyError) as exc:
            self._send_json(400, {"error": str(exc)})


if __name__ == "__main__":
    print("Eluno POS web app + mock on http://localhost:{}".format(PORT))
    ThreadingHTTPServer(("0.0.0.0", PORT), WebHandler).serve_forever()
