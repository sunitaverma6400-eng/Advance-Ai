"""Stark Family relay (Flask). Holds all API keys; the Android app never sees them.

Endpoints: GET /health (open), GET /characters, GET /brain_status, POST /chat  (auth: X-App-Token)
Run with ONE gunicorn worker so key cooldown state is shared (see render.yaml).
"""
import hmac
import logging
import os
import time

from flask import Flask, jsonify, request

from prompting import (build_messages, clean_history, language_ok, load_personas,
                       rewrite_request)
from router import AllBusy, Router, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("relay")

BASE = os.path.dirname(os.path.abspath(__file__))


def create_app(router=None, personas=None, token=None):
    app = Flask(__name__)
    app.json.ensure_ascii = False  # keep Devanagari readable in responses and logs
    personas = personas or load_personas()
    router = router or Router(load_config(), state_path=os.environ.get("STATE_PATH", os.path.join(BASE, "state.json")))
    app_token = token if token is not None else os.environ.get("APP_TOKEN", "")

    def guard():
        if not app_token:
            return jsonify(error="server_not_configured", detail="APP_TOKEN missing on relay"), 503
        given = request.headers.get("X-App-Token", "")
        if not hmac.compare_digest(given.encode(), app_token.encode()):
            return jsonify(error="unauthorized"), 401
        return None

    @app.get("/health")
    def health():
        return jsonify(ok=True, time=int(time.time()), characters=list(personas))

    @app.get("/characters")
    def characters():
        if (r := guard()) is not None:
            return r
        return jsonify([{"id": p["id"], "name": p["name"], "role": p["role"]} for p in personas.values()])

    @app.get("/brain_status")
    def brain_status():
        if (r := guard()) is not None:
            return r
        return jsonify(router.status(list(personas)))

    @app.post("/chat")
    def chat():
        if (r := guard()) is not None:
            return r
        data = request.get_json(silent=True) or {}
        cid = str(data.get("character") or "jarvis").lower()
        persona = personas.get(cid)
        if persona is None:
            return jsonify(error="unknown_character", known=list(personas)), 404
        message = str(data.get("message") or "").strip()[:4000]
        if not message:
            return jsonify(error="empty_message"), 400

        messages = build_messages(persona, message, clean_history(data.get("history")),
                                  data.get("memory"), data.get("context"))
        tier = router.tier_for(cid)
        t0 = time.time()
        base = {"character": cid, "tool_calls": [], "proposed_rule": None, "mood_delta": 0}
        try:
            res = router.complete(tier, messages)
        except AllBusy as e:
            log.warning("all busy char=%s errors=%s", cid, e.errors[-4:])
            return jsonify(speech=persona["busy_line"], degraded=True, retry_after_s=e.retry_after_s,
                           language_ok=True, provider=None, model=None, **base)

        text, lang_ok = res.text, language_ok(res.text)
        if not lang_ok:  # language guard: rewrite once on the stronger tier
            try:
                fix = router.complete("smart", messages + rewrite_request(res.text))
                if language_ok(fix.text):
                    text, lang_ok, res = fix.text, True, fix
            except AllBusy:
                pass
        log.info("chat char=%s tier=%s provider=%s model=%s attempts=%d lang_ok=%s ms=%d",
                 cid, tier, res.provider, res.model, res.attempts, lang_ok, (time.time() - t0) * 1000)
        return jsonify(speech=text, degraded=False, retry_after_s=0, language_ok=lang_ok,
                       provider=res.provider, model=res.model, attempts=res.attempts,
                       latency_ms=int((time.time() - t0) * 1000), **base)

    return app


app = create_app()
