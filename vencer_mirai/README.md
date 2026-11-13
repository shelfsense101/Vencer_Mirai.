# Vencer Mirai

A Flask + SQLite retail management app with per-store accounts, multilingual interface, inventory, expiry alerts, sales, supplier quote comparison, manually observed competitor prices, market observations, headline feed, availability, demand-based purchase suggestions, wastage tracking, event planning, reports and a rule-based business advisor.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000. Set a strong `SECRET_KEY` environment variable before deployment. Render settings: Root Directory `SHELFSENSE` only if you place these files in that folder; Build `pip install -r requirements.txt`; Start `gunicorn app:app`.

## Important data notes
- Existing database filename remains `shelfsense_v2.db` to support the previous app's data. Back up the database before replacing code.
- Supplier quotations and competitor prices are entered by the shop owner; the app does not fabricate contacts or local prices.
- Market headlines are fetched from a public Google News RSS search when network access is available. They are general news context, not verified local wholesale rates.
- Demand estimates use the shop's recorded sales. The 30-day reorder suggestion is a simple transparent heuristic, not a machine-learning prediction.
- The eight language selector is wired to user accounts. Some less-common interface phrases fall back to English; core navigation and common labels have translations in all eight languages.
- Use HTTPS in production. The built-in CSRF check protects POST forms and the barcode API.
