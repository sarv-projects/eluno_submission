# offer logic reads offers.json, retail can edit an offer without a code change
# stackable can go together, non-stackable fights alone, we pick whatever saves most
# manager discount only if someone asks for it, reason + approver required, never silent

from itertools import combinations
from .money import percentage


class OfferError(ValueError):
    pass


def _tagset(tags):
    if isinstance(tags, str):
        return {x.strip() for x in tags.split(",") if x.strip()}
    return set(tags or [])


def _eligible(offer, lines, customer_tags, sale_date, staff, manager_percent, reason_code, approver_id):
    if not offer.get("active", True):
        return False, "inactive"
    if sale_date < offer["valid_from"] or sale_date > offer["valid_to"]:
        return False, "outside valid dates"
    rule = offer.get("rule", {})
    cond = rule.get("condition", {})
    tags = _tagset(customer_tags)
    if cond.get("customer_tag") and cond["customer_tag"] not in tags:
        return False, "customer lacks tag {}".format(cond["customer_tag"])
    lenses = [x for x in lines if x["line_type"] == "lens"]
    if cond.get("lens_type") and not any(x["lens"].get("lens_type") == cond["lens_type"] for x in lenses):
        return False, "cart has no {} lens".format(cond["lens_type"])
    if cond.get("index_min") is not None and not any(float(x["lens"].get("index", 0)) >= float(cond["index_min"]) for x in lenses):
        return False, "lens index is below {}".format(cond["index_min"])
    if cond.get("coating") and not any(x["lens"].get("coating") == cond["coating"] for x in lenses):
        return False, "cart has no {} coating".format(cond["coating"])
    pair_groups = {}
    for line in lines:
        pair_groups.setdefault(str(line["pair_ref"]), set()).add(line["line_type"])
    valid_pairs = [k for k, kinds in pair_groups.items() if "frame" in kinds and "lens" in kinds]
    if int(cond.get("min_pairs_in_cart", 0)) > len(valid_pairs):
        return False, "requires at least {} complete pairs".format(cond["min_pairs_in_cart"])
    if cond.get("has_frame_and_lens") and not valid_pairs:
        return False, "requires a frame and lens in the same pair"
    req = rule.get("requires", {})
    if req:
        if not manager_percent:
            return False, "manager discount was not requested"
        if not (0 < manager_percent <= int(rule["discount"].get("value_bps_max", 0))):
            return False, "manager discount is above the limit for this store"
        if not staff or staff.get("role") != req.get("role") or not staff.get("can_override_discount"):
            return False, "requires an authorized store manager"
        if not reason_code or not approver_id:
            return False, "requires reason code and approver identity"
        if approver_id != staff.get("staff_id"):
            return False, "approver identity must match the authenticated manager"
    return True, "eligible"


def _target_indices(offer, lines):
    scope = offer["rule"].get("applies_to", "cart")
    cond = offer["rule"].get("condition", {})
    if scope == "frame":
        return [i for i, x in enumerate(lines) if x["line_type"] == "frame"]
    if scope == "lens":
        candidates = [i for i, x in enumerate(lines) if x["line_type"] == "lens"]
        if offer["rule"].get("discount", {}).get("on") == "lowest_priced_lens_line" and candidates:
            return [min(candidates, key=lambda i: (lines[i]["unit_price_paise"], i))]
        if cond.get("lens_type"):
            candidates = [i for i in candidates if lines[i]["lens"].get("lens_type") == cond["lens_type"]]
        if cond.get("index_min") is not None:
            candidates = [i for i in candidates if float(lines[i]["lens"].get("index", 0)) >= float(cond["index_min"])]
        if cond.get("coating"):
            candidates = [i for i in candidates if lines[i]["lens"].get("coating") == cond["coating"]]
        return candidates
    if scope == "pair":
        groups = {}
        for i, line in enumerate(lines):
            groups.setdefault(str(line["pair_ref"]), []).append(i)
        indices = []
        for key in sorted(groups):
            group = groups[key]
            kinds = {lines[i]["line_type"] for i in group}
            if "frame" in kinds and "lens" in kinds:
                indices.extend(group)
        return indices
    return list(range(len(lines)))


def _allocate_flat(amount, indices, balances):
    # split a flat offer across lines, leftover paisa goes lowest index first
    amount = min(int(amount), sum(balances[i] for i in indices))
    if amount <= 0 or not indices:
        return {}
    total = sum(balances[i] for i in indices)
    if not total:
        return {}
    raw = [(i, amount * balances[i] // total, amount * balances[i] % total) for i in indices]
    allocated = {i: base for i, base, _ in raw}
    remainder = amount - sum(allocated.values())
    for i, _, _ in sorted(raw, key=lambda x: (-x[2], x[0]))[:remainder]:
        allocated[i] += 1
    return {i: p for i, p in allocated.items() if p}


def _apply_offer(offer, lines, balances, requested_manager_percent=0):
    discount = offer["rule"]["discount"]
    indices = _target_indices(offer, lines)
    if not indices:
        return {}
    if discount["type"] == "percent":
        bps = int(discount.get("value_bps", requested_manager_percent))
        if "value_bps_max" in discount:
            bps = int(requested_manager_percent)
        alloc = {i: percentage(balances[i], bps) for i in indices}
        cap = discount.get("max_discount_paise")
        if cap is not None and sum(alloc.values()) > int(cap):
            alloc = _allocate_flat(int(cap), indices, {i: balances[i] for i in range(len(lines))})
    elif discount["type"] == "flat":
        flat = int(discount["value_paise"])
        # pair offers give the flat amount to each complete pair
        if offer["rule"].get("applies_to") == "pair":
            groups = {}
            for i in indices:
                groups.setdefault(str(lines[i]["pair_ref"]), []).append(i)
            alloc = {}
            for group in groups.values():
                current = {i: balances[i] for i in range(len(lines))}
                alloc.update(_allocate_flat(flat, group, current))
        else:
            alloc = _allocate_flat(flat, indices, {i: balances[i] for i in range(len(lines))})
    else:
        raise OfferError("unsupported discount type: {}".format(discount.get("type")))
    # never take more than the line still has
    return {i: min(p, balances[i]) for i, p in alloc.items() if p > 0}


def evaluate_offers(lines, offers, customer_tags, sale_date, staff=None,
                    manager_percent=0, reason_code=None, approver_id=None):
    # every offer in a combo has to be stackable, non-stackable ones compete alone
    # best customer saving wins, tie -> fewer offers -> id order. priority only sets
    # the order they get applied to the lines, not who gets picked
    eligible = []
    explanations = []
    mandatory = None
    if manager_percent:
        manager_rules = [x for x in offers if x.get("rule", {}).get("requires")]
        if not manager_rules:
            raise OfferError("manager discount is not configured")
        max_bps = min(int(x["rule"]["discount"].get("value_bps_max", 0)) for x in manager_rules)
        if int(manager_percent) <= 0 or int(manager_percent) > max_bps:
            raise OfferError("manager discount must be between 0 and {} basis points".format(max_bps))
        if not staff or staff.get("role") != "store_manager" or not staff.get("can_override_discount"):
            raise OfferError("manager discount requires an authorized store manager")
        if not reason_code or not approver_id or approver_id != staff.get("staff_id"):
            raise OfferError("manager discount requires matching approver identity and reason code")
        # a manager asked for this on the floor, so it goes in whatever set we pick
        mandatory = manager_rules[0]
    for offer in offers:
        ok, why = _eligible(offer, lines, customer_tags, sale_date, staff,
                            manager_percent, reason_code, approver_id)
        if ok and _target_indices(offer, lines):
            eligible.append(offer)
        else:
            explanations.append({"offer_id": offer.get("offer_id"), "applied": False, "why": why})

    stackable = [x for x in eligible if x.get("stackable")]
    candidates = [()] + [(x,) for x in eligible if not x.get("stackable")]
    for size in range(1, len(stackable) + 1):
        candidates.extend(combinations(stackable, size))
    if mandatory is not None:
        if not any(any(x.get("offer_id") == mandatory.get("offer_id") for x in c) for c in candidates):
            raise OfferError("requested manager discount is not eligible for this cart")
        candidates = [c for c in candidates
                      if any(x.get("offer_id") == mandatory.get("offer_id") for x in c)]

    best = (0, 0, (), [], [x["unit_price_paise"] for x in lines])
    for candidate in candidates:
        chosen = sorted(candidate, key=lambda x: (int(x.get("priority", 0)), x["offer_id"]))
        balances = [int(x["unit_price_paise"]) for x in lines]
        allocations = []
        total = 0
        for offer in chosen:
            alloc = _apply_offer(offer, lines, balances, manager_percent)
            amount = sum(alloc.values())
            for i, p in alloc.items():
                balances[i] -= p
            allocations.append((offer, alloc))
            total += amount
        ids = tuple(x["offer_id"] for x in chosen)
        # maximize savings, then fewer offers, then stable ID order
        score = (total, -len(chosen))
        if score > best[:2] or (score == best[:2] and ids < best[2]):
            best = (total, -len(chosen), ids, allocations, balances)

    _, _, _, allocations, balances = best
    applied = []
    line_allocs = [[] for _ in lines]
    for app_index, (offer, alloc) in enumerate(allocations):
        amount = sum(alloc.values())
        if amount <= 0:
            continue
        applied.append({"offer_id": offer["offer_id"], "name": offer.get("name", offer["offer_id"]),
                        "amount_paise": amount, "priority": int(offer.get("priority", 0)),
                        "stackable": bool(offer.get("stackable"))})
        for i, p in alloc.items():
            line_allocs[i].append({"amount_paise": p, "discount_application_index": len(applied) - 1})
    applied_ids = {x["offer_id"] for x in applied}
    for offer in eligible:
        if offer["offer_id"] not in applied_ids:
            explanations.append({"offer_id": offer["offer_id"], "applied": False,
                                 "why": "not applied, another allowed combination saved more"})
    for x in applied:
        why = ("manager override, approved by {}".format(approver_id)
               if mandatory is not None and x["offer_id"] == mandatory.get("offer_id")
               else "applied, it saved the most")
        explanations.append({"offer_id": x["offer_id"], "applied": True,
                             "why": why, "amount_paise": x["amount_paise"]})
    return {"applied": applied, "line_allocations": line_allocs,
            "discounted_unit_paise": balances, "explanations": explanations}




