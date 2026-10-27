# full flow against the real mock, starts it on a free port itself
# covers checkout idempotency, refund restock, channel split, rx blocks

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pos.adapters import HttpClient, ShopifyAdapter, StoreToolAdapter, TerminalAdapter
from pos.service import PosService, DomainError, event_date
from pos.storage import Store

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK=os.path.join(ROOT,"starter-kit","mock-apis","mock_server.py")
DATA=os.path.join(ROOT,"starter-kit","data")

class MockFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sock=socket.socket(); sock.bind(("127.0.0.1",0)); cls.port=sock.getsockname()[1]; sock.close()
        cls.proc=subprocess.Popen([sys.executable,MOCK,"--port",str(cls.port),"--no-flake"],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        url="http://127.0.0.1:{}/health".format(cls.port)
        for _ in range(60):
            if cls.proc.poll() is not None: raise RuntimeError("mock server exited")
            try:
                with urllib.request.urlopen(url,timeout=0.2): break
            except Exception: time.sleep(0.1)
        else: raise RuntimeError("mock server did not start")
        cls.http=HttpClient("http://127.0.0.1:{}".format(cls.port),sleeper=lambda _:None)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=5)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(os.path.join(self.tmp.name,"state.sqlite3"))
        self.svc=PosService(ShopifyAdapter(self.http),StoreToolAdapter(self.http),TerminalAdapter(self.http),self.store,DATA)

    def tearDown(self): self.tmp.cleanup()

    def test_invalid_expired_and_off_grid_prescriptions_block(self):
        with self.assertRaisesRegex(DomainError,"expired"):
            self.svc.quote("+919000000000","HSR","S105",[[91000003,91000083,None]],sale_date="2026-10-27")
        with self.assertRaisesRegex(DomainError,"axis 87"):
            self.svc.quote("+919000000003","HSR","S105",[[91000003,91000083,"RX5999"]],sale_date="2026-10-27")

    def test_lens_sphere_cylinder_and_add_limits_are_actionable(self):
        lens={"eluno":{"lens_type":"single_vision","index":1.5,"coating":"standard","rx_limits":{"sph_min":-4.0,"sph_max":4.0,"cyl_abs_max":2.0,"add_required":False}}}
        rx={"rx_id":"RX-TEST","od":{"sph":-6.0,"cyl":-3.0,"axis":90,"add":None},"os":{"sph":0.0,"cyl":0.0,"axis":None,"add":None}}
        with self.assertRaisesRegex(DomainError,"OD sphere -6.0"):
            self.svc._validate_lens(rx,lens)
        progressive={"eluno":{"lens_type":"progressive","index":1.6,"coating":"standard","rx_limits":{"sph_min":-4.0,"sph_max":4.0,"cyl_abs_max":2.0,"add_required":True,"add_min":0.75,"add_max":3.0}}}
        rx["od"]["sph"]=-1.0; rx["od"]["cyl"]=-0.5
        with self.assertRaisesRegex(DomainError,"OD ADD is required"):
            self.svc._validate_lens(rx,progressive)
    def test_checkout_idempotent_refund_restock_and_day_report(self):
        shop=ShopifyAdapter(self.http)
        before=shop.inventory([92000006],70000002)[0]["available"]
        quote=self.svc.quote("+919000000005","HSR","S105",[[91000003,91000083,None]],sale_date="2026-10-27")
        self.assertEqual(quote["discount_paise"],180000)
        result=self.svc.checkout("+919000000005","HSR","S105",[[91000003,91000083,None]],sale_date="2026-10-27",checkout_key="IT-CHECKOUT-1")
        self.assertEqual(result["status"],"complete")
        order=shop.order(result["shopify_order_id"])
        self.assertEqual(order["financial_status"],"paid")
        lens=next(x for x in order["line_items"] if any(p["name"]=="line_type" and p["value"]=="lens" for p in x["properties"]))
        self.assertIn("rx_id",{p["name"] for p in lens["properties"]})
        self.assertEqual(order["taxes_included"],True)
        self.assertEqual(sum(int(round(float(t["price"])*100)) for li in order["line_items"] for t in li["tax_lines"]),int(round(float(order["total_tax"])*100)))
        after_sale=shop.inventory([92000003],70000002)[0]["available"]
        self.assertEqual(after_sale,before-1)
        repeated=self.svc.checkout("+919000000005","HSR","S105",[[91000003,91000083,None]],sale_date="2026-10-27",checkout_key="IT-CHECKOUT-1")
        self.assertEqual(repeated["shopify_order_id"],result["shopify_order_id"])
        refunded=self.svc.refund(result["invoice_no"],"HSR","Customer changed mind","CHANGED_MIND","IT-REFUND-1")
        self.assertEqual(refunded["status"],"complete")
        self.assertEqual(refunded["amount_paise"],result["total_paise"])
        self.assertIn("/CN/",refunded["credit_note_no"])
        after_refund=shop.inventory([92000003],70000002)[0]["available"]
        self.assertEqual(after_refund,before)
        remote=shop.order(result["shopify_order_id"])
        self.assertEqual(remote["financial_status"],"refunded")
        rf=remote["refunds"][0]
        types={p["value"] for line in rf["refund_line_items"] for original in remote["line_items"] if original["id"]==line["line_item_id"] for p in original["properties"] if p["name"]=="line_type"}
        self.assertEqual(types,{"frame","lens"})
        frame_rf=next(x for x in rf["refund_line_items"] if x["restock_type"]=="return")
        lens_rf=next(x for x in rf["refund_line_items"] if x["restock_type"]=="no_restock")
        self.assertEqual(frame_rf["location_id"],70000002)
        self.assertIsNone(lens_rf["location_id"])
        again=self.svc.refund(result["invoice_no"],"HSR","Customer changed mind","CHANGED_MIND","IT-REFUND-1")
        self.assertEqual(again["credit_note_no"],refunded["credit_note_no"])
        report=self.svc.day_report("HSR",event_date(rf["created_at"]).isoformat())
        self.assertGreaterEqual(report["refunds_paise"],result["total_paise"])

    def test_two_pairs_second_pair_offer_and_stock(self):
        shop=ShopifyAdapter(self.http)
        before=shop.inventory([92000006],70000002)[0]["available"]
        result=self.svc.checkout("+919000000005","HSR","S105",
                                 [[91000006,91000083,None],[91000006,91000083,None]],
                                 sale_date="2026-10-27",checkout_key="IT-TWO-PAIR")
        self.assertEqual(result["status"],"complete")
        order=shop.order(result["shopify_order_id"])
        self.assertEqual(len(order["line_items"]),4)
        self.assertIn("SECONDPAIR50",{a["title"] for a in order["discount_applications"]})
        self.assertEqual(shop.inventory([92000006],70000002)[0]["available"],before-2)

    def test_manager_discount_on_checkout_is_audited(self):
        result=self.svc.checkout("+919000000005","HSR","S105",[[91000003,91000083,None]],
                                 sale_date="2026-10-27",checkout_key="IT-MANAGER",
                                 manager_percent=500,reason_code="WALKOUT",approver_id="S108")
        order=ShopifyAdapter(self.http).order(result["shopify_order_id"])
        attrs={a["name"]:a["value"] for a in order["note_attributes"]}
        self.assertEqual(attrs["discount_reason_code"],"WALKOUT")
        self.assertEqual(attrs["discount_approver_id"],"S108")
        self.assertIn("MANAGER_OVERRIDE",{a["title"] for a in order["discount_applications"]})

    def test_day_report_splits_sales_by_channel(self):
        self.svc.checkout("+919000000005","HSR","S105",[[91000003,91000083,None]],
                          channel="whatsapp_booked",sale_date="2026-10-27",checkout_key="IT-CHANNEL")
        report=self.svc.day_report("HSR","2026-10-27")
        self.assertIn("whatsapp_booked",report["by_channel"])
        self.assertEqual(report["by_channel"]["whatsapp_booked"]["bill_count"],1)
        self.assertGreater(report["by_channel"]["whatsapp_booked"]["net_sales_paise"],0)
        self.assertEqual(sum(x["net_sales_paise"] for x in report["by_channel"].values()),
                         report["net_sales_paise"])
if __name__=="__main__": unittest.main()




