# the whole till flow, lookup -> quote -> charge -> shopify order -> refund -> day end
# intent saved to db before every external call, state tells us if retry is safe
# shopify order create has no idempotency, so checkout_key goes on the order and we go
# looking for it after a timeout instead of blindly posting again

import json, os, uuid, sqlite3
from datetime import date, datetime, timedelta, timezone
from .adapters import ApiError, AmbiguousRemoteError
from .money import inclusive_tax, money_string, to_paise
from .offers import evaluate_offers

IST = timezone(timedelta(hours=5, minutes=30))

class DomainError(ValueError): pass

def attr(resource, name):
    return next((str(x.get("value")) for x in resource.get("note_attributes", []) if x.get("name") == name), None)

def attrs_dict(resource):
    return {str(x.get("name")): str(x.get("value")) for x in resource.get("note_attributes", [])}

def local_date(value=None):
    return date.fromisoformat(str(value)) if value else datetime.now(IST).date()

def event_date(value):
    if not value: return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None: dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST).date()

def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as f: return json.load(f)

def property_map(line):
    return {str(p.get("name")): str(p.get("value")) for p in line.get("properties", [])}

class PosService:
    def __init__(self, shopify, store_tool, terminal, store, data_dir):
        self.shopify, self.store_tool, self.terminal, self.store = shopify, store_tool, terminal, store
        self.data_dir = data_dir
        self.offers = read_json(os.path.join(data_dir, "offers.json"))
        self.tax_rates = read_json(os.path.join(data_dir, "tax_rates.json"))["rates"]

    def _location(self, store_code):
        x = next((x for x in self.shopify.locations() if x.get("eluno", {}).get("store_code") == store_code and x.get("eluno", {}).get("kind") == "store"), None)
        if not x or not x.get("active"): raise DomainError("unknown, inactive, or non-retail store: {}".format(store_code))
        return x

    def _staff(self, store_code, staff_id):
        rows = self.store_tool.staff(store_code)
        x = next((x for x in rows if x.get("staff_id") == staff_id), None)
        if not x: raise DomainError("staff {} is not assigned to {}".format(staff_id, store_code))
        return x, rows

    def _customer(self, phone):
        rows = self.shopify.customers_by_phone(phone)
        if not rows: raise DomainError("no Shopify customer found for phone {}".format(phone))
        if len(rows) != 1: raise DomainError("multiple Shopify customers share this phone; resolve duplicate before billing")
        return rows[0]

    def _catalog(self):
        products = self.shopify.products("Frame") + self.shopify.products("Lens")
        variants = {}
        for product in products:
            if product.get("status") != "active": continue
            for variant in product.get("variants", []): variants[int(variant["id"])] = (product, variant)
        return variants

    def _prescription(self, phone, customer, day, rx_id=None):
        rows = self.store_tool.prescriptions(phone)
        if rx_id:
            rows = [x for x in rows if x.get("rx_id") == rx_id]
            if not rows: raise DomainError("prescription {} not found for this customer".format(rx_id))
        rows = [x for x in rows if int(x.get("customer_id", -1)) == int(customer["id"])]
        valid = [x for x in rows if x.get("valid_until", "") >= day.isoformat()]
        valid.sort(key=lambda x: (x.get("date", ""), x.get("rx_id", "")), reverse=True)
        if valid: return valid[0]
        if rows: raise DomainError("no valid prescription on {}; latest expired {}. Arrange a new/confirmed prescription.".format(day, max(x.get("valid_until", "unknown") for x in rows)))
        raise DomainError("no prescription found; book an eye test or verify the store-tool record")

    def _validate_lens(self, rx, variant):
        limits, lens = variant.get("eluno", {}).get("rx_limits", {}), variant.get("eluno", {})
        reasons = []
        for eye_name in ("od", "os"):
            eye = rx.get(eye_name) or {}
            sph, cyl, axis, add = eye.get("sph"), eye.get("cyl"), eye.get("axis"), eye.get("add")
            if sph is None or not float(limits.get("sph_min", -999)) <= float(sph) <= float(limits.get("sph_max", 999)):
                reasons.append("{} sphere {} outside {} to {} D; choose a wider-range lens or ask optometrist".format(eye_name.upper(), sph, limits.get("sph_min"), limits.get("sph_max")))
            if cyl is None or abs(float(cyl)) > float(limits.get("cyl_abs_max", 999)):
                reasons.append("{} cylinder {} exceeds ±{} D; select a suitable lens or refer to optometrist".format(eye_name.upper(), cyl, limits.get("cyl_abs_max")))
            if limits.get("add_required") and add is None:
                reasons.append("{} ADD is required for {}; select single vision or obtain confirmed ADD".format(eye_name.upper(), lens.get("lens_type")))
            if limits.get("add_required") and add is not None and not float(limits.get("add_min", -999)) <= float(add) <= float(limits.get("add_max", 999)):
                reasons.append("{} ADD {} outside {} to {} D for this lens".format(eye_name.upper(), add, limits.get("add_min"), limits.get("add_max")))
            if axis is not None and (int(axis) < 0 or int(axis) > 180): reasons.append("{} axis {} outside 0–180°; verify source prescription".format(eye_name.upper(), axis))
            elif axis is not None and int(axis) % 5: reasons.append("{} axis {}° is not on 5° grid; do not round at the till, ask the optometrist to confirm".format(eye_name.upper(), axis))
        if reasons:
            raise DomainError("Prescription {} cannot be dispensed with {} index {} / {}:\n - {}".format(rx["rx_id"], lens.get("lens_type"), lens.get("index"), lens.get("coating"), "\n - ".join(reasons)))

    def _tax_rate(self, hsn, day):
        rows = [x for x in self.tax_rates if str(x["hsn"]) == str(hsn) and x["effective_from"] <= day.isoformat() and (not x.get("effective_to") or day.isoformat() <= x["effective_to"])]
        if not rows: raise DomainError("no effective GST rate for HSN {} on {}".format(hsn, day))
        return int(max(rows, key=lambda x: x["effective_from"])["rate_bps"])

    def _remote_invoice_max(self, store_code, prefix):
        loc = self._location(store_code); maximum = 0
        for order in self.shopify.orders(location_id=loc["id"], limit=250):
            inv = attr(order, "invoice_no") or ""
            if inv.startswith(prefix) and inv[len(prefix):].isdigit(): maximum = max(maximum, int(inv[len(prefix):]))
        return maximum

    def _remote_credit_max(self, store_code, prefix):
        loc = self._location(store_code); maximum = 0; stem = prefix + "CN/"
        for order in self.shopify.orders(location_id=loc["id"], limit=250):
            for refund in order.get("refunds", []):
                cn = attr(refund, "credit_note_no") or ""
                if cn.startswith(stem) and cn[len(stem):].isdigit(): maximum = max(maximum, int(cn[len(stem):]))
        return maximum

    def quote(self, phone, store_code, staff_id, pairs, channel="walk_in", payment_method="upi", sale_date=None, manager_percent=0, reason_code=None, approver_id=None):
        day = local_date(sale_date)
        if channel not in ("walk_in", "whatsapp_booked", "online_pickup"): raise DomainError("channel must be walk_in, whatsapp_booked or online_pickup")
        if payment_method not in ("cash", "card", "upi"): raise DomainError("payment method must be cash, card or upi")
        if not pairs: raise DomainError("provide at least one FRAME_VARIANT:LENS_VARIANT[:RX_ID] pair")
        location = self._location(store_code)
        cashier, store_staff = self._staff(store_code, staff_id)
        customer = self._customer(phone); catalog = self._catalog(); rx_cache = {}; lines = []; requirements = {}
        for index, pair in enumerate(pairs, 1):
            frame_id, lens_id, rx_id = pair
            if int(frame_id) not in catalog: raise DomainError("frame variant {} missing or archived".format(frame_id))
            if int(lens_id) not in catalog: raise DomainError("lens variant {} missing or archived".format(lens_id))
            fp, frame = catalog[int(frame_id)]; lp, lens = catalog[int(lens_id)]
            if frame.get("eluno", {}).get("line_type") != "frame": raise DomainError("variant {} is not a frame".format(frame_id))
            if not frame.get("eluno", {}).get("rx_able"): raise DomainError("frame {} is not prescription-capable".format(frame.get("sku")))
            if lens.get("eluno", {}).get("line_type") != "lens": raise DomainError("variant {} is not a lens".format(lens_id))
            if rx_id not in rx_cache: rx_cache[rx_id] = self._prescription(phone, customer, day, rx_id)
            rx = rx_cache[rx_id]; self._validate_lens(rx, lens)
            item_id = frame.get("inventory_item_id")
            if item_id is None: raise DomainError("frame {} has no tracked inventory item".format(frame.get("sku")))
            requirements[int(item_id)] = requirements.get(int(item_id), 0) + 1
            lines.extend([
                {"line_type":"frame", "pair_ref":str(index), "product":fp, "variant":frame, "unit_price_paise":to_paise(frame["price"]), "rx":rx},
                {"line_type":"lens", "pair_ref":str(index), "product":lp, "variant":lens, "unit_price_paise":to_paise(lens["price"]), "rx":rx, "lens":lens.get("eluno", {})},
            ])
        levels = self.shopify.inventory(list(requirements), location["id"])
        stock = {int(x["inventory_item_id"]):int(x["available"]) for x in levels}
        for item_id, qty in requirements.items():
            if stock.get(item_id, 0) < qty: raise DomainError("insufficient frame stock at {} (item {}: {}, need {})".format(store_code, item_id, stock.get(item_id, 0), qty))
        manager = next((x for x in store_staff if x.get("staff_id") == approver_id), None) if manager_percent else None
        if manager_percent and not manager: raise DomainError("approver {} is not on this store's staff roster".format(approver_id))
        rule_lines = [{"line_type":x["line_type"], "pair_ref":x["pair_ref"], "unit_price_paise":x["unit_price_paise"], "lens":x.get("lens", {})} for x in lines]
        offer_result = evaluate_offers(rule_lines, self.offers, customer.get("tags", ""), day.isoformat(), manager, manager_percent, reason_code, approver_id)
        order_lines=[]; line_totals=[]; gross_total=discount_total=tax_total=taxable_total=0
        for i, line in enumerate(lines):
            variant=line["variant"]; hsn=variant.get("eluno", {}).get("hsn"); rate=self._tax_rate(hsn, day)
            discount=sum(x["amount_paise"] for x in offer_result["line_allocations"][i]); gross=line["unit_price_paise"]
            tx=inclusive_tax(gross-discount, rate); gross_total+=gross; discount_total+=discount; tax_total+=tx["tax_paise"]; taxable_total+=tx["taxable_paise"]
            props=[{"name":"line_type","value":line["line_type"]},{"name":"hsn","value":str(hsn)},{"name":"pair_ref","value":line["pair_ref"]}]
            if line["line_type"]=="lens":
                rx=line["rx"]; lens=variant["eluno"]
                props.extend([{"name":"rx_id","value":rx["rx_id"]},{"name":"rx_od","value":json.dumps(rx.get("od",{}),separators=(",",":"))},{"name":"rx_os","value":json.dumps(rx.get("os",{}),separators=(",",":"))},{"name":"rx_pd","value":json.dumps(rx.get("pd",{}),separators=(",",":"))},{"name":"lens_config","value":"{}/{}/{}".format(lens.get("lens_type"),lens.get("index"),lens.get("coating"))}])
            allocs=[{"amount":money_string(x["amount_paise"]),"discount_application_index":x["discount_application_index"]} for x in offer_result["line_allocations"][i]]
            order_lines.append({"variant_id":int(variant["id"]),"quantity":1,"title":line["product"].get("title","Eluno item"),"price":money_string(gross),"total_discount":money_string(discount),"taxable":True,"tax_lines":[{"title":"CGST","rate":(rate/2)/10000,"price":money_string(tx["cgst_paise"])},{"title":"SGST","rate":(rate/2)/10000,"price":money_string(tx["sgst_paise"])}],"discount_allocations":allocs,"properties":props})
            line_totals.append({"line_type":line["line_type"],"sku":variant.get("sku"),"description":line["product"].get("title"),"pair_ref":line["pair_ref"],"gross_paise":gross,"discount_paise":discount,"taxable_paise":tx["taxable_paise"],"cgst_paise":tx["cgst_paise"],"sgst_paise":tx["sgst_paise"],"total_paise":gross-discount})
        applications=[{"type":"manual","title":x["offer_id"],"value":money_string(x["amount_paise"]),"value_type":"fixed_amount","allocation_method":"each","target_selection":"explicit","target_type":"line_item"} for x in offer_result["applied"]]
        total=gross_total-discount_total
        if total<=0: raise DomainError("discounts reduce total to zero; manager must review")
        return {"store_code":store_code,"location_id":int(location["id"]),"invoice_prefix":location["eluno"]["invoice_prefix"],"customer":customer,"cashier":cashier,"channel":channel,"payment_method":payment_method,"sale_date":day.isoformat(),"lines":line_totals,"order_lines":order_lines,"offers":offer_result["applied"],"offer_explanations":offer_result["explanations"],"gross_paise":gross_total,"discount_paise":discount_total,"taxable_paise":taxable_total,"tax_paise":tax_total,"total_paise":total,"discount_applications":applications,"manager_reason":reason_code if manager_percent else None,"approver_id":approver_id if manager_percent else None}

    def _order_payload(self, quote, invoice_no, key, charge):
        attrs=[{"name":"invoice_no","value":invoice_no},{"name":"store_code","value":quote["store_code"]},{"name":"channel","value":quote["channel"]},{"name":"staff_id","value":quote["cashier"]["staff_id"]},{"name":"payment_method","value":quote["payment_method"]},{"name":"checkout_key","value":key},{"name":"terminal_charge_id","value":charge["charge_id"]}]
        if quote.get("manager_reason"):
            attrs += [{"name":"discount_reason_code","value":quote["manager_reason"]},{"name":"discount_approver_id","value":quote["approver_id"]}]
        return {"taxes_included":True,"location_id":quote["location_id"],"inventory_behaviour":"decrement_obeying_policy","source_name":"eluno-pos","customer":{"id":int(quote["customer"]["id"])},"note":"POS checkout key {}".format(key),"tags":"pos,{}".format(quote["channel"]),"note_attributes":attrs,"line_items":quote["order_lines"],"discount_applications":quote["discount_applications"],"transactions":[{"kind":"sale","status":"success","gateway":"razorpay_terminal","amount":money_string(quote["total_paise"]),"authorization":charge["charge_id"]}]}

    def _find_remote_checkout(self, location_id, key):
        for order in self.shopify.orders(location_id=location_id, limit=250):
            if attr(order,"checkout_key")==key: return order
        return None

    def _save_checkout_payload(self, key, wrapper):
        with sqlite3.connect(self.store.path) as db:
            db.execute("UPDATE checkouts SET payload_json=?,updated_at=? WHERE checkout_key=?",(json.dumps(wrapper,separators=(",",":")),datetime.now(timezone.utc).isoformat(timespec="seconds"),key))

    def _finish_order(self, key, row, order):
        self.store.update_checkout(key,"complete",order_id=int(order["id"]),error=None)
        return {"status":"complete","checkout_key":key,"invoice_no":row["invoice_no"],"shopify_order_id":int(order["id"]),"shopify_order_number":order.get("order_number"),"total_paise":to_paise(order["total_price"]),"charge_id":row.get("charge_id")}

    def checkout(self, phone, store_code, staff_id, pairs, channel="walk_in", payment_method="upi", sale_date=None, manager_percent=0, reason_code=None, approver_id=None, checkout_key=None):
        key=checkout_key or str(uuid.uuid4()); day=local_date(sale_date)
        pairs=[list(x) for x in pairs]
        signature={"phone":phone,"store_code":store_code,"staff_id":staff_id,"pairs":pairs,"channel":channel,"payment_method":payment_method,"sale_date":day.isoformat(),"manager_percent":int(manager_percent),"reason_code":reason_code,"approver_id":approver_id}
        existing=self.store.get_checkout(key)
        if existing:
            wrapper=json.loads(existing["payload_json"])
            if wrapper.get("signature")!=signature: raise DomainError("checkout key belongs to a different cart; use original inputs or a new key")
            quote=wrapper["quote"]
            if existing["status"]=="complete": return self._finish_order(key,existing,self.shopify.order(existing["order_id"]))
            if existing["status"]=="prepared": return self._charge_and_create(key,existing,wrapper,quote)
            if existing["status"] in ("needs_reconciliation","void","void_refunded","charge_pending","shopify_create_pending"):
                if existing["status"]=="shopify_create_pending":
                    remote=self._find_remote_checkout(int(quote["location_id"]),key)
                    if remote: return self._finish_order(key,existing,remote)
                raise DomainError("checkout {} is {}; reconcile terminal/Shopify before retry. {}".format(key,existing["status"],existing.get("error") or ""))
            if existing["status"]=="charged_order_pending":
                remote=self._find_remote_checkout(int(quote["location_id"]),key)
                if remote: return self._finish_order(key,existing,remote)
                payload=wrapper.get("order_payload")
                if not payload: raise DomainError("stored checkout has no Shopify payload; manual reconciliation required")
                return self._create_order_after_charge(key,existing,quote,payload)
            raise DomainError("checkout state {} cannot be resumed".format(existing["status"]))

        quote=self.quote(phone,store_code,staff_id,pairs,channel,payment_method,day.isoformat(),manager_percent,reason_code,approver_id)
        wrapper={"signature":signature,"quote":quote}
        remote_max=self._remote_invoice_max(store_code,quote["invoice_prefix"])
        row,created=self.store.begin_checkout(key,store_code,quote["invoice_prefix"],remote_max,wrapper)
        if not created:
            wrapper=json.loads(row["payload_json"])
            if wrapper.get("signature")!=signature: raise DomainError("checkout key belongs to a different cart")
            quote=wrapper["quote"]
            if row["status"]!="prepared": raise DomainError("concurrent checkout entered state {}; reconcile before proceeding".format(row["status"]))
        return self._charge_and_create(key,row,wrapper,quote)

    def _charge_and_create(self,key,row,wrapper,quote):
        if not self.store.claim_checkout(key,"prepared","charge_pending"):
            raise DomainError("another process owns checkout {}; reconcile before continuing".format(key))
        try:
            charge=self.terminal.charge(quote["total_paise"],quote["payment_method"],key)
        except AmbiguousRemoteError as exc:
            self.store.update_checkout(key,"needs_reconciliation",error=str(exc))
            raise DomainError("terminal outcome unknown; do not charge again; reconcile reference {}".format(key))
        except ApiError as exc:
            self.store.update_checkout(key,"needs_reconciliation" if exc.status>=500 else "void",error=str(exc))
            raise DomainError("terminal charge failed HTTP {}; invoice {} is reserved/void".format(exc.status,row["invoice_no"]))
        self.store.update_checkout(key,"charged_order_pending",charge_id=charge["charge_id"])
        payload=self._order_payload(quote,row["invoice_no"],key,charge)
        wrapper["order_payload"]=payload; self._save_checkout_payload(key,wrapper)
        row=self.store.get_checkout(key)
        return self._create_order_after_charge(key,row,quote,payload)
    def _create_order_after_charge(self,key,row,quote,payload):
        if not self.store.claim_checkout(key,"charged_order_pending","shopify_create_pending"):
            raise DomainError("checkout {} is not ready for Shopify create (state {})".format(key,self.store.get_checkout(key)["status"] ))
        try:
            order=self.shopify.create_order(payload)
            return self._finish_order(key,row,order)
        except ApiError as exc:
            if exc.status==429:
                self.store.update_checkout(key,"charged_order_pending",error=str(exc))
                raise DomainError("Shopify rate limited order create; retry same checkout key {}".format(key))
            if 400<=exc.status<500:
                try:
                    result=self.terminal.refund(row["charge_id"],quote["total_paise"],"SHOPIFY_ORDER_REJECTED")
                    self.store.update_checkout(key,"void_refunded",error="Shopify rejected ({}); terminal compensation {}".format(exc.status,result.get("refund_id")))
                    raise DomainError("Shopify rejected order HTTP {}; payment compensated; invoice {} is void".format(exc.status,row["invoice_no"]))
                except DomainError: raise
                except Exception as refund_exc:
                    self.store.update_checkout(key,"needs_reconciliation",error="Shopify rejected {}; compensation failed {}".format(exc,refund_exc))
                    raise DomainError("Shopify rejected order; compensation needs reconciliation: {}".format(refund_exc))
            if exc.status>=500:
                try: remote=self._find_remote_checkout(int(quote["location_id"]),key)
                except Exception: remote=None
                if remote: return self._finish_order(key,row,remote)
            self.store.update_checkout(key,"needs_reconciliation",error=str(exc))
            raise DomainError("Shopify order outcome needs reconciliation; do not charge/create again: {}".format(exc))
        except AmbiguousRemoteError as exc:
            try: remote=self._find_remote_checkout(int(quote["location_id"]),key)
            except Exception: remote=None
            if remote: return self._finish_order(key,row,remote)
            self.store.update_checkout(key,"needs_reconciliation",error=str(exc))
            raise DomainError("Shopify create timed out; no matching order found. Reconcile key {} before retry.".format(key))

    def _find_order_by_invoice(self,invoice_no,store_code):
        loc=self._location(store_code)
        return next((x for x in self.shopify.orders(location_id=loc["id"],limit=250) if attr(x,"invoice_no")==invoice_no),None)

    def refund(self,invoice_no,store_code,reason,reason_code,refund_key=None):
        if not reason or not reason_code: raise DomainError("refund requires human-readable reason and reason code")
        key=refund_key or str(uuid.uuid4()); existing=self.store.get_refund(key)
        if existing:
            wrapper=json.loads(existing["payload_json"])
            if wrapper.get("invoice_no")!=invoice_no or wrapper.get("store_code")!=store_code: raise DomainError("refund key already belongs to another invoice")
            return self._resume_refund(key,existing,wrapper)
        order=self._find_order_by_invoice(invoice_no,store_code)
        if not order: raise DomainError("no Shopify order found for invoice {} at {}".format(invoice_no,store_code))
        sale=[x for x in order.get("transactions",[]) if x.get("kind")=="sale" and x.get("status")=="success"]
        if len(sale)!=1: raise DomainError("full refund requires exactly one successful sale transaction")
        sale_tx=sale[0]; charge_id=sale_tx.get("authorization")
        if not charge_id: raise DomainError("sale transaction lacks terminal authorization id")
        already={}
        for old in order.get("refunds",[]):
            for line in old.get("refund_line_items",[]): already[int(line["line_item_id"])]=already.get(int(line["line_item_id"]),0)+int(line["quantity"])
        refund_lines=[]
        for line in order.get("line_items",[]):
            if already.get(int(line["id"]),0): raise DomainError("order already has a refunded line; partial/multiple refunds are not supported")
            kind=property_map(line).get("line_type")
            if kind=="frame": refund_lines.append({"line_item_id":int(line["id"]),"quantity":int(line["quantity"]),"restock_type":"return","location_id":int(order["location_id"])})
            else: refund_lines.append({"line_item_id":int(line["id"]),"quantity":int(line["quantity"]),"restock_type":"no_restock"})
        calc=self.shopify.calculate_refund(int(order["id"]),refund_lines)
        suggested=calc.get("transactions",[{}])[0]
        amount=to_paise(suggested.get("amount","0.00")); maximum=to_paise(suggested.get("maximum_refundable","0.00"))
        if amount<=0 or amount>maximum: raise DomainError("calculated refund {} exceeds captured refundable {}".format(money_string(amount),money_string(maximum)))
        locations=self.shopify.locations()
        loc=next((x for x in locations if x.get("eluno",{}).get("store_code")==store_code),None)
        if not loc or not loc.get("eluno",{}).get("invoice_prefix"): raise DomainError("cannot resolve invoice prefix for {}".format(store_code))
        prefix=loc["eluno"]["invoice_prefix"]
        wrapper={"invoice_no":invoice_no,"store_code":store_code,"reason":reason,"reason_code":reason_code,"order_id":int(order["id"]),"charge_id":charge_id,"sale_transaction_id":int(sale_tx["id"]),"amount_paise":amount,"maximum_refundable_paise":maximum,"refund_lines":refund_lines}
        remote_max=self._remote_credit_max(store_code,prefix)
        row,_=self.store.reserve_credit_note(store_code,prefix,remote_max,key,int(order["id"]),invoice_no,wrapper,amount)
        wrapper=json.loads(row["payload_json"]); wrapper.update({"reason":reason,"reason_code":reason_code})
        wrapper["shopify_refund_payload"]={"note":reason,"note_attributes":[{"name":"credit_note_no","value":row["credit_note_no"]},{"name":"reason_code","value":reason_code},{"name":"refund_key","value":key}],"refund_line_items":refund_lines,"transactions":[{"kind":"refund","gateway":sale_tx.get("gateway","razorpay_terminal"),"amount":money_string(amount),"parent_id":int(sale_tx["id"])}]}
        self._save_refund_payload(key,wrapper)
        return self._resume_refund(key,self.store.get_refund(key),wrapper)

    def _save_refund_payload(self,key,wrapper):
        with sqlite3.connect(self.store.path) as db:
            db.execute("UPDATE refund_intents SET payload_json=?,updated_at=? WHERE refund_key=?",(json.dumps(wrapper,separators=(",",":")),datetime.now(timezone.utc).isoformat(timespec="seconds"),key))

    def _remote_refund(self,order_id,key):
        order=self.shopify.order(order_id)
        return next((x for x in order.get("refunds",[]) if attr(x,"refund_key")==key),None)

    def _resume_refund(self,key,row,wrapper):
        if row["status"]=="complete": return {"status":"complete","refund_key":key,"invoice_no":row["invoice_no"],"credit_note_no":row["credit_note_no"],"amount_paise":int(row["amount_paise"])}
        try: remote=self._remote_refund(wrapper["order_id"],key)
        except Exception: remote=None
        if remote:
            self.store.update_refund(key,"complete")
            return {"status":"complete","refund_key":key,"invoice_no":row["invoice_no"],"credit_note_no":row["credit_note_no"],"amount_paise":int(row["amount_paise"]),"shopify_refund_id":remote.get("id")}
        status=row["status"]
        if status in ("needs_reconciliation","terminal_pending"):
            raise DomainError("refund {} may have reached terminal; do not repeat terminal call. Reconcile terminal and Shopify.".format(key))
        if status=="prepared":
            self.store.update_refund(key,"terminal_pending")
            try:
                result=self.terminal.refund(wrapper["charge_id"],int(wrapper["amount_paise"]),wrapper["reason_code"])
                self.store.update_refund(key,"terminal_succeeded",terminal_refund_id=result.get("refund_id"))
            except AmbiguousRemoteError as exc:
                self.store.update_refund(key,"needs_reconciliation",error=str(exc))
                raise DomainError("terminal refund outcome ambiguous; reconcile before retrying key {}".format(key))
            except ApiError as exc:
                self.store.update_refund(key,"failed",error=str(exc))
                raise DomainError("terminal rejected refund HTTP {}; no Shopify refund created".format(exc.status))
        status=self.store.get_refund(key)["status"]
        if status=="terminal_succeeded":
            try:
                response=self.shopify.create_refund(int(wrapper["order_id"]),wrapper["shopify_refund_payload"])
                self.store.update_refund(key,"complete")
                return {"status":"complete","refund_key":key,"invoice_no":row["invoice_no"],"credit_note_no":row["credit_note_no"],"amount_paise":int(row["amount_paise"]),"shopify_refund_id":response.get("id") if response else None}
            except ApiError as exc:
                if exc.status==429:
                    self.store.update_refund(key,"shopify_refund_pending",error=str(exc))
                    raise DomainError("terminal refund succeeded; Shopify rate limited. Retry same refund key {}".format(key))
                if exc.status>=500:
                    try: remote=self._remote_refund(wrapper["order_id"],key)
                    except Exception: remote=None
                    if remote:
                        self.store.update_refund(key,"complete")
                        return {"status":"complete","refund_key":key,"invoice_no":row["invoice_no"],"credit_note_no":row["credit_note_no"],"amount_paise":int(row["amount_paise"]),"shopify_refund_id":remote.get("id")}
                self.store.update_refund(key,"needs_reconciliation",error=str(exc))
                raise DomainError("terminal refund succeeded but Shopify refund requires reconciliation: {}".format(key))
            except AmbiguousRemoteError as exc:
                try: remote=self._remote_refund(wrapper["order_id"],key)
                except Exception: remote=None
                if remote:
                    self.store.update_refund(key,"complete")
                    return {"status":"complete","refund_key":key,"invoice_no":row["invoice_no"],"credit_note_no":row["credit_note_no"],"amount_paise":int(row["amount_paise"]),"shopify_refund_id":remote.get("id")}
                self.store.update_refund(key,"needs_reconciliation",error=str(exc))
                raise DomainError("Shopify refund outcome ambiguous after terminal refund; reconcile key {}".format(key))
        if status=="shopify_refund_pending":
            try:
                response=self.shopify.create_refund(int(wrapper["order_id"]),wrapper["shopify_refund_payload"])
                self.store.update_refund(key,"complete")
                return {"status":"complete","refund_key":key,"invoice_no":row["invoice_no"],"credit_note_no":row["credit_note_no"],"amount_paise":int(row["amount_paise"]),"shopify_refund_id":response.get("id") if response else None}
            except ApiError as exc:
                if exc.status==429:
                    self.store.update_refund(key,"shopify_refund_pending",error=str(exc))
                    raise DomainError("Shopify rate limited again; retry same refund key {}".format(key))
                if exc.status>=500:
                    try: remote=self._remote_refund(wrapper["order_id"],key)
                    except Exception: remote=None
                    if remote:
                        self.store.update_refund(key,"complete")
                        return {"status":"complete","refund_key":key,"invoice_no":row["invoice_no"],"credit_note_no":row["credit_note_no"],"amount_paise":int(row["amount_paise"]),"shopify_refund_id":remote.get("id")}
                self.store.update_refund(key,"needs_reconciliation",error=str(exc)); raise DomainError("Shopify refund pending needs reconciliation: {}".format(key))
        raise DomainError("refund {} is in state {}; manual reconciliation required".format(key,self.store.get_refund(key)["status"]))

    def day_report(self,store_code,report_date):
        day=local_date(report_date); location=self._location(store_code)
        orders=self.shopify.orders(location_id=location["id"],limit=250)
        bills=[]; refund_rows=[]
        for order in orders:
            if order.get("cancelled_at") or not attr(order,"invoice_no"): continue
            if event_date(order.get("created_at"))==day: bills.append(order)
            for refund in order.get("refunds",[]):
                if event_date(refund.get("created_at"))==day: refund_rows.append((order,refund))
        gross=taxable=tax=discounts=net_sales=0; payments={}; channels={}
        def bucket(code):
            return channels.setdefault(code,{"bill_count":0,"gross_paise":0,"discounts_paise":0,"net_sales_paise":0,"refunds_paise":0})
        for order in bills:
            order_gross=order_disc=order_tax=order_taxable=0
            for line in order.get("line_items",[]):
                line_gross=to_paise(line.get("price","0.00"))*int(line.get("quantity",1))
                line_disc=to_paise(line.get("total_discount","0.00"))
                line_tax=sum(to_paise(t.get("price","0.00")) for t in line.get("tax_lines",[]))
                order_gross+=line_gross; order_disc+=line_disc; order_tax+=line_tax
                order_taxable+=max(0,line_gross-line_disc-line_tax)
            order_net=to_paise(order.get("total_price","0.00"))
            gross+=order_gross; discounts+=order_disc; tax+=order_tax; taxable+=order_taxable; net_sales+=order_net
            channel=attr(order,"channel") or "unknown"
            b=bucket(channel)
            b["bill_count"]+=1; b["gross_paise"]+=order_gross
            b["discounts_paise"]+=order_disc; b["net_sales_paise"]+=order_net
            method=attr(order,"payment_method") or "unknown"
            paid=sum(to_paise(t.get("amount","0.00")) for t in order.get("transactions",[]) if t.get("kind")=="sale" and t.get("status")=="success")
            payments[method]=payments.get(method,0)+paid
        refunds=refund_tax=refund_taxable=0; refunds_by_method={}
        for order,refund in refund_rows:
            amount=sum(to_paise(t.get("amount","0.00")) for t in refund.get("transactions",[]) if t.get("kind")=="refund")
            refunds+=amount
            for line in refund.get("refund_line_items",[]):
                refund_taxable+=to_paise(line.get("subtotal","0.00")); refund_tax+=to_paise(line.get("total_tax","0.00"))
            method=attr(order,"payment_method") or "unknown"
            refunds_by_method[method]=refunds_by_method.get(method,0)+amount
            bucket(attr(order,"channel") or "unknown")["refunds_paise"]+=amount
        return {"store_code":store_code,"location_id":int(location["id"]),"business_date":day.isoformat(),"bill_count":len(bills),"gross_paise":gross,"discounts_paise":discounts,"net_sales_paise":net_sales,"taxable_paise":taxable,"tax_paise":tax,"refunds_paise":refunds,"refund_taxable_paise":refund_taxable,"refund_tax_paise":refund_tax,"net_after_refunds_paise":net_sales-refunds,"payments_by_method_paise":payments,"refunds_by_method_paise":refunds_by_method,"by_channel":channels,"shopify_order_numbers":[x.get("order_number") for x in bills],"notes":["refunds count on the day they happened, numbers come from Shopify","channel comes from the channel saved on each order","the mock order list stops at 250, so older bills can be missed"]}
