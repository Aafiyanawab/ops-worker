"""Simulated company environment: a vendor portal + an internal ERP.
Deliberate traps: flaky first request (503), unsorted list, human-format dates,
strict ERP validation, duplicate detection, ambiguous vendor names."""
from datetime import datetime
from flask import Flask, request, redirect, session, jsonify, render_template_string

INVOICES = [
    ("INV-2041", "Northwind Traders", 48250.00, "2026-08-12", "2026-09-11"),
    ("INV-2087", "Northwind Traders", 51780.50, "2026-09-18", "2026-10-18"),
    ("INV-1990", "Northwind Traders", 47900.00, "2026-07-03", "2026-08-02"),
    ("INV-3010", "Contoso Ltd", 120000.00, "2026-09-25", "2026-10-25"),
    ("INV-4001", "Acme Corp", 9800.00, "2026-09-01", "2026-10-01"),
    ("INV-4002", "Acme Industries", 15250.75, "2026-09-10", "2026-10-10"),
]
INV = [dict(id=a, vendor=b, amount=c, issued=d, due=e) for a, b, c, d, e in INVOICES]
human = lambda iso: datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b %Y")

PAGE = """<!doctype html><title>{{title}}</title><body>
<nav><a href="/vendor/invoices">Vendor invoices</a> | <a href="/erp/bills">ERP bills</a> | <a href="/erp/bills/new">New ERP bill</a></nav>
<h1>{{title}}</h1>{{body|safe}}</body>"""


def create_app():
    app = Flask(__name__)
    app.secret_key = "demo"
    state = {"flaked": False, "bills": []}
    page = lambda title, body, code=200: (render_template_string(PAGE, title=title, body=body), code)

    @app.route("/")
    @app.route("/vendor")
    @app.route("/vendor/")
    @app.route("/erp")
    @app.route("/erp/")
    def home():
        return page("Company intranet", '<ul><li><a href="/vendor/login">Vendor portal (login required)</a></li>'
                    '<li><a href="/erp/bills">ERP: bills</a></li><li><a href="/erp/bills/new">ERP: new bill</a></li></ul>')

    @app.route("/vendor/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            if request.form.get("username") == "ap@demo" and request.form.get("password") == "demo123":
                session["in"] = True
                return redirect("/vendor/invoices")
            return page("Vendor login", '<div class="error">Invalid credentials</div>')
        return page("Vendor login", '<form method="post"><input name="username" placeholder="Email">'
                    '<input name="password" type="password" placeholder="Password"><button type="submit">Sign in</button></form>')

    @app.route("/vendor/invoices")
    def invoices():
        if not session.get("in"):
            return redirect("/vendor/login")
        q = request.args.get("vendor", "").lower()
        rows = "".join(f'<tr><td><a href="/vendor/invoice/{i["id"]}">{i["id"]}</a></td><td>{i["vendor"]}</td>'
                       f'<td>{human(i["issued"])}</td></tr>' for i in INV if q in i["vendor"].lower())
        return page("Invoices", '<form method="get"><input name="vendor" placeholder="Filter by vendor" value="%s">'
                    '<button type="submit">Filter</button></form><table><tr><th>Invoice</th><th>Vendor</th><th>Issued</th></tr>%s</table>' % (q, rows))

    @app.route("/vendor/invoice/<iid>")
    def invoice(iid):
        if not session.get("in"):
            return redirect("/vendor/login")
        if not state["flaked"]:  # transient failure on the first invoice-detail load
            state["flaked"] = True
            return page("Service unavailable", "<p>503 temporarily unavailable, try again.</p>", 503)
        i = next((x for x in INV if x["id"] == iid), None)
        if not i:
            return page("Not found", "<p>No such invoice</p>", 404)
        return page(f"Invoice {iid}", f'<p>Vendor: {i["vendor"]}</p><p>Issued: {human(i["issued"])}</p>'
                    f'<p>Amount: ₹ {i["amount"]:,.2f}</p><p>Due date: {human(i["due"])}</p>')

    FORM = ('{err}<form method="post"><input name="vendor" placeholder="Vendor" value="{v}">'
            '<input name="invoice_no" placeholder="Invoice number" value="{n}">'
            '<input name="amount" placeholder="Amount" value="{a}">'
            '<input name="due_date" placeholder="Due date DD/MM/YYYY" value="{d}">'
            '<button type="submit">Save bill</button></form>')

    @app.route("/erp/bills/new", methods=["GET", "POST"])
    def new_bill():
        f = request.form
        v, n, a, d = (f.get(k, "").strip() for k in ("vendor", "invoice_no", "amount", "due_date"))
        err = ""
        if request.method == "POST":
            try:
                due = datetime.strptime(d, "%d/%m/%Y").date().isoformat()
            except ValueError:
                due = None
            try:
                amt = float(a)
                assert amt > 0
            except (ValueError, AssertionError):
                amt = None
            if not (v and n):
                err = "Vendor and invoice number are required"
            elif amt is None:
                err = "Amount must be a plain positive number (no symbols or commas)"
            elif due is None:
                err = "Due date must be in DD/MM/YYYY format"
            elif any(b["invoice_no"] == n for b in state["bills"]):
                err = f"Duplicate invoice_no {n}"
            if not err:
                state["bills"].append(dict(id=len(state["bills"]) + 1, vendor=v, invoice_no=n, amount=amt, due_date=due, status="Draft"))
                return redirect("/erp/bills")
            err = f'<div class="error">{err}</div>'
        return page("New ERP bill", FORM.format(err=err, v=v, n=n, a=a, d=d))

    @app.route("/erp/bills")
    def bills():
        rows = "".join(f'<tr><td>{b["invoice_no"]}</td><td>{b["vendor"]}</td><td>{b["amount"]}</td>'
                       f'<td>{b["due_date"]}</td><td>{b["status"]}</td></tr>' for b in state["bills"])
        return page("ERP bills", f"<table><tr><th>Invoice</th><th>Vendor</th><th>Amount</th><th>Due</th><th>Status</th></tr>{rows}</table>")

    @app.route("/erp/api/bills")
    def api_bills():
        n = request.args.get("invoice_no")
        return jsonify([b for b in state["bills"] if not n or b["invoice_no"] == n])

    return app
