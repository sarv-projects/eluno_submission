# http calls live here and nowhere else, core code never sees shopify json types
# 429 carries retry-after so honour it, terminal 503 is safe to retry with same order_ref
# a timeout mid-write throws AmbiguousRemoteError, caller reconciles instead of retrying

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class ApiError(RuntimeError):
    def __init__(self, status, body, headers=None, path=None):
        self.status, self.body, self.headers, self.path = status, body, headers or {}, path
        super().__init__("HTTP {} for {}: {}".format(status, path, body))


class AmbiguousRemoteError(RuntimeError):
    """A write may have reached the remote system; reconcile before retry."""
    pass


class HttpClient:
    def __init__(self, base_url, timeout=8, attempts=5, sleeper=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.attempts = attempts
        self.sleeper = sleeper

    def request(self, method, path, payload=None, retry_terminal_503=False):
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        url = self.base_url + path
        for attempt in range(self.attempts):
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    raw = response.read()
                    return json.loads(raw.decode("utf-8")) if raw else {}
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    data = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    data = raw.decode("utf-8", "replace")
                retry_after = exc.headers.get("Retry-After", "1")
                retry = exc.code == 429 or (retry_terminal_503 and exc.code == 503)
                if retry and attempt + 1 < self.attempts:
                    try:
                        delay = min(3.0, max(0.0, float(retry_after)))
                    except ValueError:
                        delay = 1.0
                    self.sleeper(delay)
                    continue
                raise ApiError(exc.code, data, dict(exc.headers.items()), path)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if method.upper() == "GET":
                    raise RuntimeError("GET {} failed: {}".format(path, exc))
                raise AmbiguousRemoteError("write to {} may have succeeded: {}".format(path, exc))
        raise RuntimeError("retry loop exhausted for {}".format(path))


class ShopifyAdapter:
    def __init__(self, http):
        self.http = http
        self.api = "/admin/api/2026-10/"

    def _get(self, route):
        return self.http.request("GET", self.api + route)

    def _post(self, route, payload):
        return self.http.request("POST", self.api + route, payload)

    def customers_by_phone(self, phone):
        query = urllib.parse.urlencode({"query": "phone:" + phone})
        return self._get("customers/search.json?" + query).get("customers", [])

    def locations(self):
        return self._get("locations.json").get("locations", [])

    def products(self, product_type):
        query = urllib.parse.urlencode({"product_type": product_type, "limit": 250})
        return self._get("products.json?" + query).get("products", [])

    def inventory(self, item_ids, location_id):
        query = urllib.parse.urlencode({"inventory_item_ids": ",".join(str(x) for x in item_ids),
                                        "location_ids": str(location_id)})
        return self._get("inventory_levels.json?" + query).get("inventory_levels", [])

    def orders(self, location_id=None, limit=250):
        params = {"status": "any", "limit": str(limit)}
        if location_id is not None:
            params["location_id"] = str(location_id)
        query = urllib.parse.urlencode(params)
        return self._get("orders.json?" + query).get("orders", [])

    def order(self, order_id):
        return self._get("orders/{}.json".format(int(order_id))).get("order")

    def create_order(self, payload):
        return self._post("orders.json", {"order": payload}).get("order")

    def calculate_refund(self, order_id, refund_line_items):
        response = self._post("orders/{}/refunds/calculate.json".format(int(order_id)),
                              {"refund": {"refund_line_items": refund_line_items}})
        return response["refund"]

    def create_refund(self, order_id, payload):
        return self._post("orders/{}/refunds.json".format(int(order_id)), {"refund": payload}).get("refund")


class StoreToolAdapter:
    def __init__(self, http):
        self.http = http

    def prescriptions(self, phone):
        query = urllib.parse.urlencode({"phone": phone})
        return self.http.request("GET", "/smt/prescriptions?" + query).get("prescriptions", [])

    def staff(self, store_code):
        query = urllib.parse.urlencode({"store": store_code})
        return self.http.request("GET", "/smt/staff?" + query).get("staff", [])


class TerminalAdapter:
    def __init__(self, http):
        self.http = http

    def charge(self, amount_paise, method, order_ref):
        return self.http.request("POST", "/terminal/charges",
                                 {"amount_paise": int(amount_paise), "method": method, "order_ref": order_ref},
                                 retry_terminal_503=True)

    def refund(self, charge_id, amount_paise, reason):
        # Refund endpoint has no idempotency/status lookup contract; never auto-retry.
        return self.http.request("POST", "/terminal/refunds",
                                 {"charge_id": charge_id, "amount_paise": int(amount_paise), "reason": reason})
