// browser side of the till. talks to server.py, which runs the mock too.
// plain js, no build step. keep it readable for whoever picks this up next.

const $ = (id) => document.getElementById(id);
const CART_KEY = "eluno-cart";
const state = { locations: [], cart: [] };

function money(paise) {
  const v = Math.abs(Number(paise) || 0);
  const rupees = Math.floor(v / 100).toLocaleString("en-IN");
  return (Number(paise) < 0 ? "-" : "") + "\u20b9" + rupees + "." + String(v % 100).padStart(2, "0");
}

function today() {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return d.getFullYear() + "-" + m + "-" + day;
}

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function text(el, message) {
  const node = $(el);
  node.className = "out";
  node.textContent = message;
}

function html(el, markup) {
  const node = $(el);
  node.className = "out";
  node.innerHTML = markup;
}

function fail(el, message) {
  const node = $(el);
  node.className = "out error";
  node.textContent = message;
}

// stop a second click landing while a request is in flight
async function busy(buttonId, label, work) {
  const btn = $(buttonId);
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = label;
  try {
    return await work();
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function api(path, options) {
  const res = await fetch(path, options);
  const raw = await res.text();
  let data = {};
  try { data = raw ? JSON.parse(raw) : {}; } catch (e) { data = { error: raw }; }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function post(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function saveCart() {
  try { sessionStorage.setItem(CART_KEY, JSON.stringify(state.cart)); } catch (e) { /* private mode */ }
}

function restoreCart() {
  try {
    const raw = sessionStorage.getItem(CART_KEY);
    if (raw) state.cart = JSON.parse(raw) || [];
  } catch (e) { state.cart = []; }
}

async function loadStores() {
  const data = await api("/api/locations");
  // only real stores can bill, HQ is a lab location
  state.locations = (data.locations || []).filter((loc) => (loc.eluno || {}).kind === "store");
  const sel = $("store");
  const report = $("reportStore");
  sel.innerHTML = "";
  report.innerHTML = "";
  state.locations.forEach((loc) => {
    const code = loc.eluno.store_code;
    const label = code + " - " + loc.name;
    sel.add(new Option(label, code));
    report.add(new Option(label, code));
  });
  if (state.locations.some((loc) => loc.eluno.store_code === "HSR")) {
    sel.value = "HSR";
    report.value = "HSR";
  }
  await loadCashiers();
  await loadCatalog();
}

async function loadCashiers() {
  const store = $("store").value;
  const data = await api("/api/staff?store=" + encodeURIComponent(store));
  const sel = $("staff");
  sel.innerHTML = "";
  (data.staff || []).forEach((s) => {
    sel.add(new Option(s.staff_id + " (" + s.role + ")", s.staff_id));
  });
  $("reportStore").value = store;
}

async function loadCatalog() {
  const frames = await api("/api/products?product_type=Frame");
  const lenses = await api("/api/products?product_type=Lens");
  fillVariants($("frameVariant"), frames.products || []);
  fillVariants($("lensVariant"), lenses.products || []);
}

function fillVariants(select, products) {
  select.innerHTML = "";
  products.forEach((product) => {
    (product.variants || []).forEach((variant) => {
      const opt = document.createElement("option");
      opt.value = variant.id;
      opt.textContent = product.title + " / " + variant.title + " (" + variant.sku + ")";
      opt.dataset.label = product.title + " " + variant.title;
      opt.dataset.item = variant.inventory_item_id || "";
      select.appendChild(opt);
    });
  });
}

async function findCustomer() {
  const phone = $("phone").value.trim();
  try {
    const data = await api("/api/customers?phone=" + encodeURIComponent(phone));
    const customers = data.customers || [];
    if (!customers.length) {
      text("customerInfo", "No customer with that phone number.");
      text("rxInfo", "");
      return;
    }
    const c = customers[0];
    text("customerInfo", [c.first_name, c.last_name].filter(Boolean).join(" ") +
      "  |  id " + c.id + "  |  tags: " + (c.tags || "none"));
    const rxData = await api("/api/prescriptions?phone=" + encodeURIComponent(phone));
    const rows = (rxData.prescriptions || []).map((rx) =>
      rx.rx_id + "  valid until " + rx.valid_until + "  (" + rx.source + ")");
    text("rxInfo", rows.length ? "Prescriptions:\n" + rows.join("\n") : "No prescriptions on file.");
  } catch (e) {
    fail("customerInfo", e.message);
  }
}

function addPair() {
  const frameSelect = $("frameVariant");
  const lensSelect = $("lensVariant");
  if (!frameSelect.value || !lensSelect.value) {
    text("inventoryInfo", "Catalog has not loaded yet. Wait a moment and try again.");
    return;
  }
  state.cart.push({
    frame: Number(frameSelect.value),
    lens: Number(lensSelect.value),
    rx: $("rxId").value.trim() || null,
    item: frameSelect.selectedOptions[0].dataset.item,
    frameLabel: frameSelect.selectedOptions[0].dataset.label,
    lensLabel: lensSelect.selectedOptions[0].dataset.label,
  });
  saveCart();
  renderCart();
  refreshStock();
}

function renderCart() {
  const body = $("cartTable").querySelector("tbody");
  body.innerHTML = "";
  $("cartCount").textContent = state.cart.length
    ? "(" + state.cart.length + (state.cart.length === 1 ? " pair)" : " pairs)")
    : "";
  if (!state.cart.length) {
    body.innerHTML = "<tr><td colspan='4' class='hint'>Cart is empty. Add a pair above.</td></tr>";
    return;
  }
  state.cart.forEach((pair, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + esc(pair.frameLabel) + "</td>" +
      "<td>" + esc(pair.lensLabel) + "</td>" +
      "<td>" + esc(pair.rx || "latest valid") + "</td>" +
      "<td><button class='link' data-i='" + i + "'>remove</button></td>";
    body.appendChild(tr);
  });
  body.querySelectorAll("button.link").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.cart.splice(Number(btn.dataset.i), 1);
      saveCart();
      renderCart();
      refreshStock();
    });
  });
}

async function refreshStock() {
  if (!state.cart.length) { text("inventoryInfo", ""); return; }
  const store = $("store").value;
  const loc = state.locations.find((x) => (x.eluno || {}).store_code === store);
  if (!loc) return;
  const wanted = {};
  state.cart.forEach((p) => {
    if (!p.item) return;
    if (!wanted[p.item]) wanted[p.item] = { label: p.frameLabel, qty: 0 };
    wanted[p.item].qty += 1;
  });
  const itemIds = Object.keys(wanted).join(",");
  if (!itemIds) { text("inventoryInfo", ""); return; }
  try {
    const data = await api("/api/inventory?item_ids=" + itemIds + "&location_id=" + loc.id);
    const rows = (data.inventory_levels || []).map((row) => {
      const want = wanted[String(row.inventory_item_id)];
      const label = want ? want.label : "item " + row.inventory_item_id;
      const short = want && row.available < want.qty ? " - not enough for this bill" : "";
      return label + ": " + row.available + " in stock" + short;
    });
    text("inventoryInfo", rows.join("\n"));
  } catch (e) {
    text("inventoryInfo", "");
  }
}

function billBody() {
  return {
    phone: $("phone").value.trim(),
    store: $("store").value,
    staff: $("staff").value,
    pairs: state.cart.map((p) => [p.frame, p.lens, p.rx]),
    channel: $("channel").value,
    payment: $("payment").value,
    date: $("saleDate").value,
    manager_percent: Math.round(parseFloat($("managerPct").value || "0") * 100),
    reason_code: $("reasonCode").value.trim() || null,
    approver_id: $("approverId").value.trim() || null,
  };
}

const NO_CART = "Cart is empty. Add a pair in step 3 first.";

function renderQuote(q) {
  const lines = (q.lines || []).map((line) =>
    "<tr><td>" + esc(line.description) + " <span class='tag'>" + esc(line.line_type) + "</span></td>" +
    "<td>" + money(line.gross_paise) + "</td>" +
    "<td>" + money(line.discount_paise) + "</td>" +
    "<td>" + money(line.taxable_paise) + "</td>" +
    "<td>" + money(line.cgst_paise) + "</td>" +
    "<td>" + money(line.sgst_paise) + "</td></tr>").join("");

  const applied = (q.offers || []).map((o) =>
    "<li>" + esc(o.name) + " (" + esc(o.offer_id) + ") <b>" + money(o.amount_paise) + "</b></li>").join("");
  const skipped = (q.offer_explanations || [])
    .filter((x) => !x.applied)
    .map((x) => "<li>" + esc(x.offer_id) + ": " + esc(x.why) + "</li>").join("");

  html("quoteOut",
    "<table><thead><tr><th>Item</th><th>Price</th><th>Discount</th><th>Taxable</th><th>CGST</th><th>SGST</th></tr></thead>" +
    "<tbody>" + lines + "</tbody></table>" +
    "<p class='total'>To pay: <b>" + money(q.total_paise) + "</b></p>" +
    "<p class='hint'>Gross " + money(q.gross_paise) + ", discounts " + money(q.discount_paise) +
    ", tax " + money(q.tax_paise) + "</p>" +
    "<p><b>Offers applied</b></p><ul>" + (applied || "<li>none</li>") + "</ul>" +
    (skipped ? "<p><b>Not applied</b></p><ul class='hint'>" + skipped + "</ul>" : ""));
}

async function getQuote() {
  if (!state.cart.length) { fail("quoteOut", NO_CART); return; }
  await busy("quoteBtn", "Pricing...", async () => {
    try {
      renderQuote(await post("/api/quote", billBody()));
    } catch (e) {
      fail("quoteOut", e.message);
    }
  });
}

async function doCheckout() {
  if (!state.cart.length) { fail("checkoutOut", NO_CART); return; }
  const body = billBody();
  body.checkout_key = $("checkoutKey").value.trim() || null;
  await busy("checkoutBtn", "Charging...", async () => {
    try {
      const result = await post("/api/checkout", body);
      html("checkoutOut",
        "<p><b>Invoice " + esc(result.invoice_no) + "</b></p>" +
        "<p>Shopify order " + esc(result.shopify_order_number) + " (id " + esc(result.shopify_order_id) + ")</p>" +
        "<p>Charged " + money(result.total_paise) + " on terminal, charge " + esc(result.charge_id) + "</p>" +
        "<p class='hint'>Status: " + esc(result.status) + "</p>");
      // the bill is done, so clear the cart to stop an accidental second charge
      state.cart = [];
      saveCart();
      renderCart();
      text("inventoryInfo", "");
      if (result.invoice_no) $("refundInvoice").value = result.invoice_no;
      $("refundCard").scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (e) {
      fail("checkoutOut", e.message);
    }
  });
}

async function doRefund() {
  const invoice = $("refundInvoice").value.trim();
  if (!invoice) { fail("refundOut", "Enter the invoice number to refund."); return; }
  await busy("refundBtn", "Refunding...", async () => {
    try {
      const result = await post("/api/refund", {
        invoice: invoice,
        store: $("store").value,
        reason: $("refundReason").value.trim(),
        reason_code: $("refundReasonCode").value.trim(),
        refund_key: $("refundKey").value.trim() || null,
      });
      html("refundOut",
        "<p><b>Credit note " + esc(result.credit_note_no) + "</b></p>" +
        "<p>Refunded " + money(result.amount_paise) + " for invoice " + esc(result.invoice_no) + "</p>" +
        "<p class='hint'>Frame restocked, lens not. Status: " + esc(result.status) + "</p>");
    } catch (e) {
      fail("refundOut", e.message);
    }
  });
}

async function runReport() {
  await busy("reportBtn", "Reading...", async () => {
    try {
      const store = encodeURIComponent($("reportStore").value);
      const day = encodeURIComponent($("reportDate").value);
      const r = await api("/api/report?store=" + store + "&date=" + day);

      const methods = Object.entries(r.payments_by_method_paise || {})
        .map(([k, v]) => "<li>" + esc(k) + ": " + money(v) + "</li>").join("");
      const refundMethods = Object.entries(r.refunds_by_method_paise || {})
        .filter(([, v]) => v)
        .map(([k, v]) => "<li>" + esc(k) + ": " + money(v) + "</li>").join("");
      const channels = Object.entries(r.by_channel || {})
        .map(([k, v]) => "<tr><td>" + esc(k) + "</td><td>" + v.bill_count + "</td><td>" +
          money(v.net_sales_paise) + "</td><td>" + money(v.refunds_paise) + "</td></tr>").join("");

      html("reportOut",
        "<p><b>" + esc(r.store_code) + " on " + esc(r.business_date) + "</b> - " + r.bill_count +
        (r.bill_count === 1 ? " bill" : " bills") + "</p>" +
        "<p>Gross " + money(r.gross_paise) + " | discounts " + money(r.discounts_paise) +
        " | net " + money(r.net_sales_paise) + "</p>" +
        "<p>Taxable " + money(r.taxable_paise) + " | tax " + money(r.tax_paise) +
        " | refunds " + money(r.refunds_paise) + "</p>" +
        "<p><b>Net after refunds: " + money(r.net_after_refunds_paise) + "</b></p>" +
        "<p><b>Sales by payment</b></p><ul>" + (methods || "<li>none</li>") + "</ul>" +
        (refundMethods ? "<p><b>Refunds by payment</b></p><ul>" + refundMethods + "</ul>" : "") +
        "<table><thead><tr><th>Channel</th><th>Bills</th><th>Net</th><th>Refunds</th></tr></thead><tbody>" +
        (channels || "<tr><td colspan='4'>no bills</td></tr>") + "</tbody></table>");
    } catch (e) {
      fail("reportOut", e.message);
    }
  });
}

$("store").addEventListener("change", async () => {
  await loadCashiers();
  refreshStock();
});
$("lookupCustomer").addEventListener("click", () => busy("lookupCustomer", "Looking up...", findCustomer));
$("addPair").addEventListener("click", addPair);
$("quoteBtn").addEventListener("click", getQuote);
$("checkoutBtn").addEventListener("click", doCheckout);
$("refundBtn").addEventListener("click", doRefund);
$("reportBtn").addEventListener("click", runReport);

$("saleDate").value = today();
$("reportDate").value = today();
restoreCart();
renderCart();

// the free tier sleeps, so retry the first load instead of dying on one failure
async function boot() {
  for (let attempt = 1; attempt <= 6; attempt++) {
    try {
      await loadStores();
      await findCustomer();
      refreshStock();
      return;
    } catch (e) {
      if (attempt === 6) {
        fail("customerInfo", "Could not reach the server: " + e.message + ". Reload the page in a minute.");
        return;
      }
      text("customerInfo", "Waking the server up, retrying (" + attempt + "/5)...");
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
}

boot();
