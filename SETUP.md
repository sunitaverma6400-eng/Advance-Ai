# Stark Family — Milestone 1 setup

## 1. Render (relay)
New > Blueprint > pick this repo (render.yaml is read automatically), or New > Web Service with
Root Directory `relay`, Build `pip install -r requirements.txt`,
Start `gunicorn app:app --workers 1 --threads 8 --timeout 90 --bind 0.0.0.0:$PORT`.

Environment variables (Render dashboard > Environment):

| Name | Value |
|---|---|
| APP_TOKEN | any long random string (also typed once in the app) |
| GROQ_KEYS | `["gsk_key1","gsk_key2",...]` |
| GEMINI_KEYS | `["AIza_key1","AIza_key2",...]` |
| OPENROUTER_KEYS | `["sk-or-key1",...]` |

Each key in a plain list gets its own quota group (right when every key is from a different
Google project / Groq org). Keys that share one project/org: `[{"key":"k1","group":"mail1"},{"key":"k2","group":"mail1"}]`.

Model names: `relay/config/models.json` (edit + push, no code change).

## 2. APK
Push to `main` -> GitHub Actions builds it. Get it from the repo's Releases page
(tag `debug-latest`) or Actions > run > Artifacts.

## 3. First run
Open app > settings (gear) > paste Relay URL (https://<name>.onrender.com) and APP_TOKEN.

## Tests (relay)
`cd relay && python -m unittest discover -s tests`
