const $ = (id) => document.getElementById(id);
const state = { customer: null, prescriptions: [], cart: [], locations: [], frames: [], lenses: [] };

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { error: text }; }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function show(el, value) {
  const node = $(el);
  if (typeof value === "string") node.textContent = value;
  else node.textContent = JSON.stringify(value, null, 2);
}

async function loadLocations() {
  const data = await api("/api/locations");
  state.locations = data.locations || [];
  const sel = $("store");
  sel.innerHTML = "";
  state.locations.forEach((loc) => {
    const opt = document.createElement("option");
    opt.value = loc.eluno.store_code;
    opt.textContent = loc.eluno.store_code + " - " + loc.name;
    sel.appendChild(opt);
  });
  await loadStaff();
  await loadCatalog();
}

async function loadStaff() {
  const store = $("store").value;
  const data = await api("/api/staff?store=" + encodeURIComponent(store));
  const sel = $("staff");
  sel.innerHTML = "";
  (data.staff || []).forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.staff_id;
    opt.textContent = s.staff_id + " (" + s.role + ")";
    sel.appendChild(opt);
  });
  $("reportStore").value = store;
}

async function loadCatalog() {
  const frames = await api("/api/products?product_type=Frame");
  const lenses = await api("/api/products?product_type=Lens");
  state.frames = frames.products || [];
  state.lenses = lenses.products || [];
  const fSel = $("frameVariant");
  fSel.innerHTML = "";
  state.frames.forEach((p) => (p.variants || []).forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v.id;
    opt.textContent = p.title + " / " + v.title + " (" + v.sku + ")";
    fSel.appendChild(opt);
  }));
  const lSel = $("lensVariant");
  lSel.innerHTML = "";
  state.lenses.forEach((p) => (p.variants || []).forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v.id;
    opt.textContent = p.title + " / " + v.title + " (" + v.sku + ")";
    lSel.appendChild(opt);
  }));
}

async function lookupCustomer() {
  const phone = $("phone").value.trim();
  const data = await api("/api/customers?phone=" + encodeURIComponent(phone));
  const customers = data.customers || [];
  if (!customers.length) { show("customerInfo", "No customer found"); state.customer = null; return; }
  const c = customers[0];
  state.customer = c;
  show("customerInfo", c.first_name + " " + c.last_name + " (id " + c.id + ") tags: " + (c.tags || ""));
  const rxData = await api("/api/prescriptions?phone=" + encodeURIComponent(phone));
  state.prescriptions = rxData.prescriptions || [];
  const lines = state.prescriptions.map((r) => r.rx_id + " valid until " + r.valid_until + " (" + r.source + ")");
  show("rxInfo", lines.length ? "Prescriptions:\n" + lines.join("\n") : "No prescriptions");
}

async function addPair() {
  const frameId = parseInt($("frameVariant").value, 10);
  const lensId = parseInt($("lensVariant").value, 10);
  const rxId = $("rxId").value.trim() || null;
  state.cart.push({ frame: frameId, lens: lensId, rx: rxId });
  renderCart();
  await refreshInventory();
}

function renderCart() {
  const tbody = $("cartTable").querySelector("tbody");
  tbody.innerHTML = "";
  state.cart.forEach((p, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = "<td>" + p.frame + "</td><td>" + p.lens + "</td><td>" + (p.rx || "latest") + "</td><td><button data-i='" + i + "' class='remove'>x</button></td>";
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll(".remove").forEach((b) => b.addEventListener("click", (e) => {
    state.cart.splice(parseInt(e.target.dataset.i, 10), 1);
    renderCart();
  }));
}

async function refreshInventory() {
  const store = $("store").value;
  const loc = state.locations.find((l) => l.eluno.store_code === store);
  if (!loc) return;
  const ids = state.cart.map((p) => p.frame);
  if (!ids.length) { show("inventoryInfo", ""); return; }
  const data = await api("/api/inventory?item_ids=" + ids.join(",") + "&location_id=" + loc.id);
  const lines = (data.inventory_levels || []).map((l) => "item " + l.inventory_item_id + ": " + l.available + " available");
  show("inventoryInfo", lines.join("\n"));
}

function pairsPayload() {
  return state.cart.map((p) => [p.frame, p.lens, p.rx]);
}

async function quote() {
  try {
    const data = await api("/api/quote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone: $("phone").value.trim(),
        store: $("store").value,
        staff: $("staff").value,
        pairs: pairsPayload(),
        channel: $("channel").value,
        payment: $("payment").value,
        date: $("saleDate").value,
        manager_percent: Math.round(parseFloat($("managerPct").value || "0") * 100),
        reason_code: $("reasonCode").value.trim() || null,
        approver_id: $("approverId").value.trim() || null,
      }),
    });
    show("quoteOut", data);
  } catch (e) { show("quoteOut", "Error: " + e.message); }
}

async function checkout() {
  try {
    const data = await api("/api/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone: $("phone").value.trim(),
        store: $("store").value,
        staff: $("staff").value,
        pairs: pairsPayload(),
        channel: $("channel").value,
        payment: $("payment").value,
        date: $("saleDate").value,
        manager_percent: Math.round(parseFloat($("managerPct").value || "0") * 100),
        reason_code: $("reasonCode").value.trim() || null,
        approver_id: $("approverId").value.trim() || null,
        checkout_key: $("checkoutKey").value.trim() || null,
      }),
    });
    show("checkoutOut", data);
    if (data.invoice_no) $("refundInvoice").value = data.invoice_no;
  } catch (e) { show("checkoutOut", "Error: " + e.message); }
}

async function refund() {
  try {
    const data = await api("/api/refund", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        invoice: $("refundInvoice").value.trim(),
        store: $("store").value,
        reason: $("refundReason").value.trim(),
        reason_code: $("refundReasonCode").value.trim(),
        refund_key: $("refundKey").value.trim() || null,
      }),
    });
    show("refundOut", data);
  } catch (e) { show("refundOut", "Error: " + e.message); }
}

async function report() {
  try {
    const data = await api("/api/report?store=" + encodeURIComponent($("reportStore").value) + "&date=" + encodeURIComponent($("reportDate").value));
    show("reportOut", data);
  } catch (e) { show("reportOut", "Error: " + e.message); }
}

$("store").addEventListener("change", async () => { await loadStaff(); await refreshInventory(); });
$("lookupCustomer").addEventListener("click", lookupCustomer);
$("addPair").addEventListener("click", addPair);
$("quoteBtn").addEventListener("click", quote);
$("checkoutBtn").addEventListener("click", checkout);
$("refundBtn").addEventListener("click", refund);
$("reportBtn").addEventListener("click", report);

loadLocations().then(lookupCustomer);
'@ | Set-Content -LiteralPath "C:\Users\Sonali\AppData\Local\Temp\web_app.js" -Encoding utf8
echo done