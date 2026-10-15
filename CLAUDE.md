# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FastAPI service that reads Colombian traffic-related documents (cédula, factura, RUNT, certificado de gases, poder) and generic documents from PDF/images using RapidOCR, with an optional local VLM (Ollama) as fallback. It is consumed by the separate app `extracción-de-documentos-de-tránsito` (Express `server.ts` + React + Firebase Hosting/Functions), which calls it via `JETSON_API_URL` / `JETSON_API_KEY`. The README (Spanish) has the full description; user-facing text and docs are in Spanish.

## Commands

Windows dev environment with a local `venv`; production runs in Docker on a Jetson (`deploy.sh`, `docker-compose.yml`, `Caddyfile`).

- Run API locally: `Iniciar_API.bat` (loads `.env.local`, starts Ollama if `LLM_URL` is set, uvicorn on 127.0.0.1:8000); stop with `Apagar_API.bat`. Manual: `venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1`.
- Lint/types: `ruff check .` and `mypy app` (pinned in `requirements-dev.txt`).
- Tests are plain scripts, not pytest suites; they need `API_KEYS` (set by the scripts) and Windows fonts to synthesize documents:
  - `python tests/test_robustness.py` — 34 odd files; the API must never return 500 or non-JSON.
  - `python tests/bench_ocr.py all` — synthetic cédula/factura with 19 degradations.
  - `python tests/evaluate.py [filter] [--save-baseline] [--vlm]` — accuracy against `tests/samples/<name>.expected.json` (compared with `tests/baseline.json`). `tests/samples/` holds real personal data and is git-ignored.
- Temporary public access: `Iniciar_tunel_api.bat` / `Detener_tunel_api.bat` (see Tunnel below).

## Architecture

Request flow: `app/main.py` (middlewares, TrustedHost, security headers) → `app/api/routes.py` → `app/services/analyzer.py` (image/PDF → documents, no HTTP) → `app/ocr/*` (RapidOCR engine, progressive re-reads until quality is good: preprocess, orientation/skew, refocus) → `app/extraction/pipeline.py` (tries every parser in `PARSERS`, keeps the highest-confidence result) → optional `app/llm/` refinement of empty fields.

Things that need several files to understand:

- **Two modes.** `transit` returns typed documents (`documentType`, `confidence`, `needsReview`, `extractedData`); `ocr` (`/api/v1/ocr`) is generic OCR with optional `fields`/`deep` (VLM) extraction. The OCR route can use the latin PP-OCRv5 model (`OCR_REC_MODEL`, `models_extra/`); transit uses the default ch+en model.
- **Sync vs async.** `/api/v1/analyze-document` (plus legacy aliases) is synchronous. `/api/v1/jobs` (POST/GET/DELETE) runs the same work through `app/jobs.py` with `mode=transit|ocr`; the web app uses jobs to avoid proxy timeouts.
- **Per-app API keys.** `API_KEYS=<transit_key>,firebase:<key>` — an unnamed key belongs to `TRANSIT_CLIENT` (default `transito`) and enables transit-specific behavior (`is_transit` in `routes.py`). Auth, per-client/IP rate limits (`RATE_LIMIT_PER_MIN` uploads, `RATE_LIMIT_READS_PER_MIN` reads; 0 = unlimited), failed-auth lockout and per-client concurrent job caps live in `app/security.py`. The client IP comes from `client_ip()`, which trusts `CF-Connecting-IP` only when the direct peer is loopback.
- **Extraction design rule.** Fields are validated by shape in `extraction/validators.py`; an empty value beats a wrong one. LLM/VLM values must appear in the OCR text. Add a document type by creating `app/extraction/<tipo>.py` with `parse_<tipo>` and registering it in `PARSERS`.
- **Support modules:** `cache.py` (in-memory result cache), `history.py` (SQLite usage history, no file contents, `HISTORY_DB`), `progress.py`, `pagereads.py`, `app/web/panel.html` served at `/panel` when `ENABLE_PANEL=1`, and `panel_api.pyw` + `panel_ui/` (a local desktop control panel for keys/stats/upload and, in `tunnel_tab.py`, opening/closing/monitoring the tunnel; tkinter is not thread-safe, so worker threads only store state and the main thread paints it).
- Configuration is env-only (`app/config.py`); local values live in the git-ignored `.env.local`, template in `.env.example`.

## Tunnel and Firebase

- `tools/tunel_api.ps1` opens a Cloudflare Quick Tunnel (`tools/bin/cloudflared.exe`, git-ignored) **only to port 8000** with `--http-host-header 127.0.0.1:8000`, turns on rate limits (30/600) in `.env.local` while open, and restarts the API; the stop script reverses that. The URL changes every run, so the app's `JETSON_API_URL` must be updated and the app restarted.
- Fixed address: `tools/worker/worker.js` is a Cloudflare Worker (`*.workers.dev`, created by hand in the dashboard with a KV binding `TUNNEL` and secret `REGISTER_KEY`) that forwards to the current quick-tunnel URL. `tools/registrar_puente.ps1` (and the panel's Túnel tab) `POST /__tunnel` the new URL on open and `DELETE` it on close, using `TUNNEL_REGISTER_URL`/`TUNNEL_REGISTER_KEY` from `.env.local`. The app's `JETSON_API_URL` points at the Worker and never changes. Cloudflare blocks Python's default User-Agent (403), so set one. The API sees Cloudflare's IP, not the client's, behind the Worker (`client_ip()` not adjusted yet).
- ngrok, Tailscale and DigitalPlat-style free domains are not viable here (ngrok/Tailscale control hosts are blocked; free domains are risky); Kaspersky blocks PowerShell launched from JScript, so avoid tooling that does that (e.g. `wrangler login` from a hidden process).
- On this institutional network DNS does not resolve `trycloudflare.com` and TLS is intercepted; the app side works around it with `JETSON_DNS_SERVER=1.1.1.1` and `node --use-system-ca` (already in the app repo).
- `docs/PUBLICAR_EN_FIREBASE.md` is the plan for publishing the web app. Do not publish it open to everyone: the privacy/user-access items are still pending and the user-login work was explicitly cancelled. No Firebase deploy without the user's explicit action.
- Never print or commit API keys; the old key that was pasted in chat is compromised and rotated.
