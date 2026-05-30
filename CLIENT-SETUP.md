# Outbound Dashboard - per-client setup

Master template. To stand up a new client: **copy this whole folder**, then do the swaps below. Nothing else should need code changes.

```bash
cp -R ~/nv/templates/outbound-dashboard ~/nv-clients/<client>/dashboard
```

---

## 1. Secrets / env (no code change)
Set in `.env` (local) or Render Environment tab. See `.env.example` for the full list. Per-client values:

| Var | Notes |
|---|---|
| `HEYREACH_API_KEY` | client's HeyReach key |
| `HEYREACH_SENDER_IDS` | JSON array of their sender ids |
| `HEYREACH_SENDER_NAMES` | JSON map id -> display name |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | client's own Supabase project |
| `OPENROUTER_API_KEY` (or `GEMINI_API_KEY`) | can be shared or per-client |
| `PIPEDRIVE_API_TOKEN` / `PIPEDRIVE_COMPANY_DOMAIN` | if CRM sync used |
| `INSTANTLY_API_KEY` | if email outreach used |
| `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` | login creds |
| `SECRET_KEY` | fresh random string |

## 2. Branding (code edit, small)
The fork ships with OSPRI branding. Swap:

- **Logo file** — drop `static/images/<client>-logo.png`, then update the filename + alt in:
  - `templates/login.html` (line ~16, `images/ospri-logo.png`, alt="OSPRI")
  - `templates/dashboard.html` (line ~16, same)
- **Tagline / demo** — `preview.html` line ~578 `OSPRI · Performance Analytics`, and the mock-data block (~line 801) is OSPRI demo data, cosmetic only.

## 3. Infra identifiers
- `render.yaml` -> `name: outreach-dashboard` (set unique Render service name).
- **n8n workflows** hardcode the Supabase project URL `jezvwasrtgjrunagabpj.supabase.co` — swap to the client's project when importing:
  - `n8n_workflow.json` (line ~37, `/rpc/upsert_heyreach_conversation`)
  - `n8n_tag_sync_workflow.json` (line ~71, `/rpc/sync_lead_tags`)
  - also set the Supabase apikey/service-role header in those nodes.
- `gemini_evaluator.py` line ~22 `APP_REFERRER` is a hardcoded onrender URL (OpenRouter referer header, cosmetic).
- `V1.1_SETUP.md` references the old project ref + Render URL throughout — doc only, update if you reuse it as the client runbook.

## 4. Supabase schema
In the client's new Supabase project, SQL Editor -> run in order:
1. `supabase_schema.sql`
2. `RUN_THIS_IN_SUPABASE.sql`
3. `RUN_THIS_IN_SUPABASE_2.sql`

## 5. Deploy
Render (per `Procfile` / `render.yaml` / `gunicorn.conf.py`): `gunicorn app:app -c gunicorn.conf.py`.

---

## INCOMPLETE - missing from this template
These were NOT in the manual download and must be sourced from the real repo
(`Dobbin-Outbound/Render-Outbound-Dashboard`, fork of `neo-vibe/Render-Outbound-Dashboard`)
before the app will run:

- `static/js/dashboard.js`  — dashboard front-end logic, `dashboard.html` depends on it
- `static/images/ospri-logo.png` — (or just supply each client's own logo)

To complete: install `gh`, authenticate, then
`gh repo clone neo-vibe/Render-Outbound-Dashboard` and copy `static/js/` + `static/images/` over.
