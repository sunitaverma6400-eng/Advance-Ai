"""Model Router: key pools, quota groups, cooldowns, fallback chain, circuit breaker, Brain Status.

All three providers expose an OpenAI-compatible chat endpoint, so one adapter serves them all.
Keys come ONLY from environment variables (JSON list per provider) and are never logged.
"""
import hashlib
import json
import logging
import os
import random
import re
import tempfile
import threading
import time
from dataclasses import dataclass

import requests

log = logging.getLogger("router")

PROVIDERS = {
    "groq": {"url": "https://api.groq.com/openai/v1/chat/completions", "env": "GROQ_KEYS"},
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "env": "GEMINI_KEYS",
    },
    "openrouter": {"url": "https://openrouter.ai/api/v1/chat/completions", "env": "OPENROUTER_KEYS"},
}

AUTH_COOL_S = 6 * 3600
MODEL_COOL_S = 600
BAD_REQUEST_COOL_S = 120


class _Fail(Exception):
    def __init__(self, kind, status=None, retry_after=None, detail=""):
        super().__init__(f"{kind}:{status}")
        self.kind = kind
        self.status = status
        self.retry_after = retry_after
        self.detail = detail


class AllBusy(Exception):
    def __init__(self, retry_after_s, errors):
        super().__init__("all providers busy")
        self.retry_after_s = retry_after_s
        self.errors = errors


@dataclass
class Result:
    text: str
    provider: str
    model: str
    key_hint: str
    attempts: int
    tier: str


@dataclass
class KeyState:
    provider: str
    key: str
    group: str
    kid: str
    cooling_until: float = 0.0
    fails: int = 0
    req_today: int = 0
    ok_today: int = 0
    day: str = ""
    last_error: str = ""
    last_used: float = 0.0
    last_ok: float = 0.0

    @property
    def hint(self):
        return "…" + self.key[-4:]


def parse_keys(raw, provider):
    """Accepts '["k1","k2"]', '[{"key":"k1","group":"mail1"}]' or 'k1,k2'. Returns [(key, group)]."""
    if not raw or not raw.strip():
        return []
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except ValueError:
        data = [x.strip() for x in raw.split(",") if x.strip()]
    if isinstance(data, str):
        data = [data]
    out, seen = [], set()
    for i, item in enumerate(data if isinstance(data, list) else []):
        if isinstance(item, str):
            key, group = item, f"{provider}-{i + 1}"
        elif isinstance(item, dict):
            key, group = item.get("key"), item.get("group") or f"{provider}-{i + 1}"
        else:
            continue
        key = (key or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append((key, str(group)))
    return out


def _parse_retry_after(h):
    v = h.get("retry-after")
    if v is None:
        return None
    try:
        return max(1.0, min(float(v), 3600.0))
    except (TypeError, ValueError):
        return None


def _classify(status, headers, text):
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    low = (text or "").lower()
    if status == 429:
        return "rate", _parse_retry_after(h)
    if status in (401, 403):
        return "auth", None
    if status == 404 or any(s in low for s in ("decommission", "does not exist", "not found", "no longer supported")):
        return "model", None
    if status in (400, 422):
        return "bad_request", None
    if status == 408 or status >= 500:
        return "server", _parse_retry_after(h)
    return "bad_request", None


_THINK = re.compile(r"<think>.*?</think>", re.S)


def _clean(text):
    return _THINK.sub("", text or "").strip()


def load_config(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "models.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Router:
    def __init__(self, cfg, env=None, transport=None, clock=None, state_path=None):
        self.cfg = cfg
        self.transport = transport or requests.post
        self.clock = clock or time.time
        self.state_path = state_path
        self.lock = threading.RLock()
        self.model_cool = {}
        self.breaker = {p: {"fails": 0, "open_until": 0.0} for p in PROVIDERS}
        env = os.environ if env is None else env
        self.keys = {}
        for prov, meta in PROVIDERS.items():
            lst = []
            for key, group in parse_keys(env.get(meta["env"], ""), prov):
                kid = hashlib.sha256(key.encode()).hexdigest()[:10]
                lst.append(KeyState(prov, key, group, kid))
            self.keys[prov] = lst
        self._load_state()

    # ---------- helpers ----------
    def now(self):
        return self.clock()

    def _today(self):
        return time.strftime("%Y-%m-%d", time.gmtime(self.now()))

    def _roll_day(self, ks):
        today = self._today()
        if ks.day != today:
            ks.day, ks.req_today, ks.ok_today = today, 0, 0

    def tier_for(self, character):
        return self.cfg.get("characters", {}).get(character, "fast")

    @staticmethod
    def _backoff(fails, base):
        return min(900.0, base * (2 ** max(0, fails - 1))) * random.uniform(0.8, 1.2)

    # ---------- persistence (counters only, never keys) ----------
    def _load_state(self):
        if not self.state_path or not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        now = self.now()
        for lst in self.keys.values():
            for ks in lst:
                s = saved.get(ks.kid)
                if not s:
                    continue
                ks.day = s.get("day", "")
                ks.req_today = s.get("req_today", 0)
                ks.ok_today = s.get("ok_today", 0)
                ks.fails = s.get("fails", 0)
                cu = s.get("cooling_until", 0.0)
                ks.cooling_until = cu if cu > now else 0.0
                self._roll_day(ks)

    def _save_state(self):
        if not self.state_path:
            return
        data = {}
        for lst in self.keys.values():
            for ks in lst:
                data[ks.kid] = {
                    "day": ks.day, "req_today": ks.req_today, "ok_today": ks.ok_today,
                    "fails": ks.fails, "cooling_until": ks.cooling_until,
                }
        try:
            d = os.path.dirname(os.path.abspath(self.state_path))
            fd, tmp = tempfile.mkstemp(dir=d)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.state_path)
        except OSError:
            log.warning("state save failed")

    # ---------- selection ----------
    def _provider_ready(self, prov, model):
        now = self.now()
        if not self.keys.get(prov):
            return False
        if self.breaker[prov]["open_until"] > now:
            return False
        if self.model_cool.get((prov, model), 0) > now:
            return False
        return True

    def _pick_key(self, prov):
        with self.lock:
            now = self.now()
            healthy = [k for k in self.keys.get(prov, []) if k.cooling_until <= now]
            if not healthy:
                return None
            ks = min(healthy, key=lambda k: (k.last_used, k.req_today))
            ks.last_used = now
            self._roll_day(ks)
            ks.req_today += 1
            return ks

    # ---------- outcome handling ----------
    def _on_success(self, ks):
        with self.lock:
            ks.fails = 0
            ks.ok_today += 1
            ks.last_ok = self.now()
            ks.last_error = ""
            self.breaker[ks.provider]["fails"] = 0
            self._save_state()

    def _on_failure(self, ks, model, f):
        with self.lock:
            now = self.now()
            ks.last_error = f"{f.kind}" + (f" {f.status}" if f.status else "")
            br = self.breaker[ks.provider]
            if f.kind == "rate":
                ks.fails += 1
                wait = f.retry_after if f.retry_after else self._backoff(ks.fails, 20)
                for other in self.keys[ks.provider]:
                    if other.group == ks.group:  # same project/org shares one quota
                        other.cooling_until = max(other.cooling_until, now + wait)
            elif f.kind in ("server", "timeout"):
                ks.fails += 1
                wait = f.retry_after if f.retry_after else self._backoff(ks.fails, 10)
                ks.cooling_until = max(ks.cooling_until, now + wait)
            elif f.kind == "auth":
                ks.cooling_until = now + AUTH_COOL_S
            elif f.kind == "model":
                self.model_cool[(ks.provider, model)] = now + MODEL_COOL_S
            elif f.kind == "bad_request":
                self.model_cool[(ks.provider, model)] = now + BAD_REQUEST_COOL_S
            elif f.kind == "empty":
                ks.cooling_until = max(ks.cooling_until, now + 5)
            if f.kind in ("rate", "server", "timeout", "auth"):
                br["fails"] += 1
                if br["fails"] >= self.cfg["breaker"]["threshold"]:
                    br["open_until"] = now + self.cfg["breaker"]["open_s"]
                    br["fails"] = 0
                    log.warning("circuit open for %s", ks.provider)
            self._save_state()
        log.info("fail provider=%s model=%s key=%s kind=%s status=%s", ks.provider, model, ks.kid, f.kind, f.status)

    def _soonest_retry(self):
        now = self.now()
        waits = [k.cooling_until - now for lst in self.keys.values() for k in lst if k.cooling_until > now]
        waits += [b["open_until"] - now for b in self.breaker.values() if b["open_until"] > now]
        return int(max(5, min(120, min(waits)))) if waits else 15

    # ---------- the call ----------
    def _call(self, ks, model, messages, temperature, max_tokens):
        req = self.cfg["request"]
        headers = {"Authorization": f"Bearer {ks.key}", "Content-Type": "application/json"}
        if ks.provider == "openrouter":
            headers["X-Title"] = "Stark Family"
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        try:
            resp = self.transport(PROVIDERS[ks.provider]["url"], headers=headers, json=payload, timeout=req["timeout_s"])
        except requests.Timeout:
            raise _Fail("timeout", detail="timeout")
        except requests.RequestException as e:
            raise _Fail("timeout", detail=type(e).__name__)
        status = resp.status_code
        if status != 200:
            body = (getattr(resp, "text", "") or "")[:300].replace(ks.key, "***")
            kind, ra = _classify(status, resp.headers, body)
            raise _Fail(kind, status, ra, body)
        try:
            text = _clean(resp.json()["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise _Fail("empty", status, detail="bad response shape")
        if not text:
            raise _Fail("empty", status, detail="empty text")
        return text

    def complete(self, tier, messages, temperature=None, max_tokens=None):
        req = self.cfg["request"]
        temperature = req["temperature"] if temperature is None else temperature
        max_tokens = req["max_tokens"] if max_tokens is None else max_tokens
        chain = self.cfg["tiers"].get(tier) or self.cfg["tiers"]["fast"]
        total, errors = 0, []
        for entry in chain:
            prov, model = entry["provider"], entry["model"]
            if not self._provider_ready(prov, model):
                continue
            used = 0
            while used < req["max_attempts_per_provider"] and total < req["max_attempts_total"]:
                ks = self._pick_key(prov)
                if ks is None:
                    break
                used += 1
                total += 1
                try:
                    text = self._call(ks, model, messages, temperature, max_tokens)
                except _Fail as f:
                    errors.append(f"{prov}/{model}:{f.kind}")
                    self._on_failure(ks, model, f)
                    if f.kind in ("model", "bad_request"):
                        break  # this model is the problem, not the key: next chain entry
                    if not self._provider_ready(prov, model):
                        break
                    continue
                self._on_success(ks)
                log.info("ok tier=%s provider=%s model=%s key=%s attempts=%d", tier, prov, model, ks.kid, total)
                return Result(text, prov, model, ks.hint, total, tier)
            if total >= req["max_attempts_total"]:
                break
        raise AllBusy(self._soonest_retry(), errors)

    # ---------- Brain Status ----------
    def status(self, characters=()):
        now = self.now()
        out = {"time": int(now), "providers": {}, "characters": {}}
        with self.lock:
            for prov, lst in self.keys.items():
                br = self.breaker[prov]
                keys = []
                for k in lst:
                    self._roll_day(k)
                    cooling = k.cooling_until > now
                    keys.append({
                        "key": k.hint, "group": k.group,
                        "state": "cooling" if cooling else "healthy",
                        "cooling_for_s": int(k.cooling_until - now) if cooling else 0,
                        "req_today": k.req_today, "ok_today": k.ok_today,
                        "last_error": k.last_error,
                    })
                out["providers"][prov] = {
                    "configured_keys": len(lst),
                    "breaker": "open" if br["open_until"] > now else "closed",
                    "keys": keys,
                }
            for c in characters:
                tier = self.tier_for(c)
                current = None
                for e in self.cfg["tiers"].get(tier, []):
                    if self._provider_ready(e["provider"], e["model"]) and any(
                        k.cooling_until <= now for k in self.keys[e["provider"]]
                    ):
                        current = f'{e["provider"]}/{e["model"]}'
                        break
                out["characters"][c] = {"tier": tier, "model_now": current}
        return out
