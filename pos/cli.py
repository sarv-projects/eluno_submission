# just args and json printing, logic is in service.py
# quote has no side effects, checkout/refund/report talk to the mock

import argparse, json, os, sys
from decimal import Decimal, ROUND_HALF_UP
from .adapters import HttpClient, ShopifyAdapter, StoreToolAdapter, TerminalAdapter
from .money import money_string
from .service import PosService, DomainError
from .storage import Store

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA=os.path.join(ROOT,"starter-kit","data")
DEFAULT_DB=os.path.join(ROOT,"pos_state.sqlite3")

def parse_pairs(values):
    result=[]
    for value in values or []:
        parts=value.split(":")
        if len(parts) not in (2,3): raise argparse.ArgumentTypeError("pair must be FRAME_VARIANT:LENS_VARIANT[:RX_ID]")
        try: frame,lens=int(parts[0]),int(parts[1])
        except ValueError: raise argparse.ArgumentTypeError("frame and lens variant IDs must be integers")
        result.append([frame,lens,parts[2] if len(parts)==3 and parts[2] else None])
    return result

def percent_bps(text):
    try: value=Decimal(text)
    except Exception: raise argparse.ArgumentTypeError("manager discount must be a number between 0 and 10")
    if value<0 or value>10: raise argparse.ArgumentTypeError("manager discount must be between 0 and 10 percent")
    return int((value*100).quantize(Decimal("1"),rounding=ROUND_HALF_UP))

def public_quote(q):
    return {"store_code":q["store_code"],"customer":{"id":q["customer"]["id"],"name":"{} {}".format(q["customer"].get("first_name",""),q["customer"].get("last_name","" )).strip(),"tags":q["customer"].get("tags","")},"channel":q["channel"],"payment_method":q["payment_method"],"lines":[{**x,"gross":money_string(x["gross_paise"]),"discount":money_string(x["discount_paise"]),"taxable":money_string(x["taxable_paise"]),"cgst":money_string(x["cgst_paise"]),"sgst":money_string(x["sgst_paise"]),"total":money_string(x["total_paise"])} for x in q["lines"]],"offers":[{**x,"amount":money_string(x["amount_paise"])} for x in q["offers"]],"offer_explanations":q["offer_explanations"],"totals":{"gross":money_string(q["gross_paise"]),"discounts":money_string(q["discount_paise"]),"taxable":money_string(q["taxable_paise"]),"tax":money_string(q["tax_paise"]),"payable":money_string(q["total_paise"])}}

def common_checkout(p):
    pairs=parse_pairs(p.pair)
    return {"phone":p.phone,"store_code":p.store,"staff_id":p.staff,"pairs":pairs,"channel":p.channel,"payment_method":p.payment,"sale_date":p.date,"manager_percent":p.manager_discount_percent,"reason_code":p.reason_code,"approver_id":p.approver_id}

def checkout_args(parser):
    parser.add_argument("--phone",required=True,help="Shopify customer phone")
    parser.add_argument("--store",required=True,help="store code, e.g. HSR")
    parser.add_argument("--staff",required=True,help="cashier staff ID")
    parser.add_argument("--pair",action="append",required=True,help="FRAME_VARIANT:LENS_VARIANT[:RX_ID], repeat for pairs")
    parser.add_argument("--channel",choices=["walk_in","whatsapp_booked","online_pickup"],default="walk_in")
    parser.add_argument("--payment",choices=["cash","card","upi"],default="upi")
    parser.add_argument("--date",help="business date YYYY-MM-DD (default local today)")
    parser.add_argument("--manager-discount-percent",type=percent_bps,default=0)
    parser.add_argument("--reason-code",help="required for manager override")
    parser.add_argument("--approver-id",help="authorized manager staff ID")

def main(argv=None):
    parser=argparse.ArgumentParser(prog="eluno-pos",description="Eluno POS exercise CLI; Shopify remains the system of record")
    parser.add_argument("--base-url",default=os.environ.get("ELUNO_API_BASE","http://127.0.0.1:8787"))
    parser.add_argument("--db",default=DEFAULT_DB,help="local durable checkout/invoice database")
    parser.add_argument("--data-dir",default=DEFAULT_DATA,help="POS-owned offers and GST JSON directory")
    sub=parser.add_subparsers(dest="command",required=True)
    quote=sub.add_parser("quote",help="validate a cart and show offers/tax without charging")
    checkout_args(quote)
    checkout=sub.add_parser("checkout",help="charge terminal, then create Shopify order")
    checkout_args(checkout); checkout.add_argument("--checkout-key",help="stable retry key; auto-generated if omitted")
    refund=sub.add_parser("refund",help="full refund by store invoice number")
    refund.add_argument("--invoice",required=True); refund.add_argument("--store",required=True); refund.add_argument("--reason",required=True); refund.add_argument("--reason-code",required=True); refund.add_argument("--refund-key",help="stable retry/recovery key")
    report=sub.add_parser("report",help="day-end report read from Shopify")
    report.add_argument("--store",required=True); report.add_argument("--date",required=True,help="store business date YYYY-MM-DD")
    args=parser.parse_args(argv)
    try:
        http=HttpClient(args.base_url)
        svc=PosService(ShopifyAdapter(http),StoreToolAdapter(http),TerminalAdapter(http),Store(args.db),args.data_dir)
        if args.command in ("quote","checkout"):
            params=common_checkout(args)
            if args.command=="quote": result=public_quote(svc.quote(**params))
            else: result=svc.checkout(**params,checkout_key=args.checkout_key)
        elif args.command=="refund": result=svc.refund(args.invoice,args.store,args.reason,args.reason_code,args.refund_key)
        else: result=svc.day_report(args.store,args.date)
        print(json.dumps(result,indent=2,ensure_ascii=False))
        return 0
    except (DomainError,ValueError,RuntimeError) as exc:
        print(json.dumps({"error":str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=="__main__":
    sys.exit(main())

