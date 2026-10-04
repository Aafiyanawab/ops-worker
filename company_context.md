# Company context (long-term knowledge the worker is given)

- Vendor portal: {BASE}/vendor  (login: ap@demo / demo123). Invoices are listed there; issue dates and due dates are shown as "18 Sep 2026".
- Internal ERP: {BASE}/erp. Vendor bills are entered at /erp/bills/new.
- ERP rules: due_date must be DD/MM/YYYY. Amount must be a plain number (no currency symbol, no thousands separators). invoice_no must be unique.
- "Latest invoice" means the most recent ISSUE date, not the first row in the list.
- Any write to the ERP needs human approval (the harness enforces this).
- If a vendor name matches more than one vendor, ask the user which one they mean. Never guess.
- Read-only ERP JSON API for verification: GET /erp/api/bills?invoice_no=<no>
