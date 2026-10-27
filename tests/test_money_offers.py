# tax and offer maths, checked against sample_invoice.txt numbers and offers.json

import json, os, unittest
from pos.money import inclusive_tax, money_string, percentage, to_paise
from pos.offers import evaluate_offers
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); DATA=os.path.join(ROOT,"starter-kit","data")
class MoneyTests(unittest.TestCase):
    def test_inclusive_tax_matches_reference_invoice(self):
        self.assertEqual(inclusive_tax(1099800,1800),{"taxable_paise":932034,"tax_paise":167766,"cgst_paise":83883,"sgst_paise":83883})
        self.assertEqual(inclusive_tax(807415,500),{"taxable_paise":768967,"tax_paise":38448,"cgst_paise":19224,"sgst_paise":19224})
    def test_half_up_and_string_money(self):
        self.assertEqual(percentage(1,5000),1); self.assertEqual(percentage(3,5000),2)
        self.assertEqual(to_paise("12.345"),1235); self.assertEqual(money_string(-12345),"-123.45")
class OfferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(DATA,"offers.json"),encoding="utf-8") as f: cls.offers=json.load(f)
    def sample_lines(self,second=False):
        rows=[{"line_type":"frame","pair_ref":"1","unit_price_paise":1099800,"lens":{}},{"line_type":"lens","pair_ref":"1","unit_price_paise":949900,"lens":{"lens_type":"progressive","index":1.6,"coating":"blue_cut"}}]
        if second: rows += [{"line_type":"frame","pair_ref":"2","unit_price_paise":899800,"lens":{}},{"line_type":"lens","pair_ref":"2","unit_price_paise":499900,"lens":{"lens_type":"single_vision","index":1.6,"coating":"standard"}}]
        return rows
    def test_best_permitted_stackable_combo_and_line_totals(self):
        lines=self.sample_lines(); result=evaluate_offers(lines,self.offers,"campaign:progressive_oct26,priority","2026-10-27")
        applied={x["offer_id"]:x["amount_paise"] for x in result["applied"]}
        self.assertEqual(applied,{"FREE_BLUECUT_PGX":100000,"PRIORITY500":50000,"BUNDLE300":30000})
        self.assertNotIn("PGX_CAMPAIGN15",applied)
        self.assertEqual(sum(sum(x["amount_paise"] for x in row) for row in result["line_allocations"]),180000)
        self.assertTrue(all(sum(x["amount_paise"] for x in row)<=lines[i]["unit_price_paise"] for i,row in enumerate(result["line_allocations"])))
    def test_second_pair_discount_targets_only_lowest_lens(self):
        result=evaluate_offers(self.sample_lines(True),self.offers,"","2026-10-27")
        offer_index=next(i for i,x in enumerate(result["applied"]) if x["offer_id"]=="SECONDPAIR50")
        self.assertEqual(result["applied"][offer_index]["amount_paise"],249950)
        self.assertEqual(sum(x["amount_paise"] for x in result["line_allocations"][3] if x["discount_application_index"]==offer_index),249950)
        self.assertEqual(sum(x["amount_paise"] for x in result["line_allocations"][1] if x["discount_application_index"]==offer_index),0)
    def test_manager_override_requires_manager_reason_and_identity(self):
        manager={"staff_id":"S108","role":"store_manager","can_override_discount":True}
        only=[x for x in self.offers if x["offer_id"]=="MANAGER_OVERRIDE"]
        result=evaluate_offers(self.sample_lines(),only,"","2026-10-27",manager,500,"WALKOUT","S108")
        self.assertIn("MANAGER_OVERRIDE",{x["offer_id"] for x in result["applied"]})
        with self.assertRaises(ValueError): evaluate_offers(self.sample_lines(),self.offers,"","2026-10-27",manager,1100,"WALKOUT","S108")
    def test_requested_manager_discount_stands_alone_with_explanation(self):
        manager={"staff_id":"S108","role":"store_manager","can_override_discount":True}
        result=evaluate_offers(self.sample_lines(),self.offers,"campaign:progressive_oct26,priority","2026-10-27",manager,500,"WALKOUT","S108")
        ids={x["offer_id"] for x in result["applied"]}
        self.assertEqual(ids,{"MANAGER_OVERRIDE"}) # rule is non-stackable, so it replaces auto offers
        applied=next(x for x in result["explanations"] if x["offer_id"]=="MANAGER_OVERRIDE")
        self.assertIn("S108",applied["why"])
        skipped=next(x for x in result["explanations"] if x["offer_id"]=="PRIORITY500")
        self.assertFalse(skipped["applied"])
if __name__=="__main__": unittest.main()

