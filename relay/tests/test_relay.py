import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from app import create_app  # noqa: E402
from prompting import hindi_ratio, language_ok, load_personas, build_system_prompt  # noqa: E402
from router import AllBusy, Result, Router, parse_keys  # noqa: E402

CFG = {
    "request": {"timeout_s": 5, "max_attempts_per_provider": 3, "max_attempts_total": 8,
                "temperature": 0.7, "max_tokens": 100},
    "breaker": {"threshold": 6, "open_s": 60},
    "tiers": {
        "fast": [{"provider": "groq", "model": "g-small"}, {"provider": "gemini", "model": "gem"},
                 {"provider": "openrouter", "model": "or:free"}],
        "smart": [{"provider": "gemini", "model": "gem"}, {"provider": "groq", "model": "g-big"}],
    },
    "characters": {"jarvis": "smart", "vision": "fast"},
}


class Resp:
    def __init__(self, status=200, text="नमस्ते सर", headers=None):
        self.status_code, self.headers, self._text = status, headers or {}, text
        self.text = text

    def json(self):
        if self.status_code != 200:
            raise ValueError
        return {"choices": [{"message": {"content": self._text}}]}


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def make(script, groq=("gk1111", "gk2222", "gk3333"), gem=("ge1111",), orr=("or1111",), clock=None):
    """script: callable(url, key, model) -> Resp | Exception"""
    calls = []

    def transport(url, headers=None, json=None, timeout=None):
        key = headers["Authorization"].split()[-1]
        calls.append((url.split("/")[2], key, json["model"]))
        out = script(url, key, json["model"])
        if isinstance(out, Exception):
            raise out
        return out

    env = {"GROQ_KEYS": __import__("json").dumps(list(groq)), "GEMINI_KEYS": __import__("json").dumps(list(gem)),
           "OPENROUTER_KEYS": __import__("json").dumps(list(orr))}
    return Router(CFG, env=env, transport=transport, clock=clock or Clock()), calls


MSG = [{"role": "user", "content": "hi"}]


class RouterTests(unittest.TestCase):
    def test_lru_rotation(self):
        r, calls = make(lambda *a: Resp())
        c = r.clock
        for _ in range(3):
            c.t += 1
            r.complete("fast", MSG)
        self.assertEqual(sorted(k for _, k, _ in calls), ["gk1111", "gk2222", "gk3333"])

    def test_429_switches_key_without_error(self):
        r, calls = make(lambda u, k, m: Resp(429, "slow", {"retry-after": "30"}) if k == "gk1111" else Resp())
        res = r.complete("fast", MSG)
        self.assertEqual(res.provider, "groq")
        self.assertNotEqual(res.key_hint, "…1111")
        st = r.status(["vision"])["providers"]["groq"]["keys"]
        cool = [k for k in st if k["state"] == "cooling"]
        self.assertEqual(len(cool), 1)
        self.assertTrue(25 <= cool[0]["cooling_for_s"] <= 30)  # Retry-After honoured

    def test_same_group_shares_cooldown(self):
        env = {"GROQ_KEYS": json.dumps([{"key": "a1111", "group": "p"}, {"key": "b2222", "group": "p"},
                                        {"key": "c3333", "group": "q"}])}
        calls = []

        def tr(url, headers=None, json=None, timeout=None):
            k = headers["Authorization"].split()[-1]
            calls.append(k)
            return Resp(429, "x", {"retry-after": "60"}) if k in ("a1111", "b2222") else Resp()

        r = Router(CFG, env=env, transport=tr, clock=Clock())
        res = r.complete("fast", MSG)
        self.assertEqual(res.key_hint, "…3333")
        self.assertEqual(len(calls), 2)  # a failed, b skipped (same quota group), c succeeded

    def test_provider_exhausted_falls_to_next(self):
        r, _ = make(lambda u, k, m: Resp(429, "x") if "groq" in u else Resp())
        res = r.complete("fast", MSG)
        self.assertEqual(res.provider, "gemini")

    def test_all_busy_raises_with_retry(self):
        r, _ = make(lambda *a: Resp(429, "x", {"retry-after": "40"}))
        with self.assertRaises(AllBusy) as cm:
            r.complete("fast", MSG)
        self.assertTrue(5 <= cm.exception.retry_after_s <= 120)

    def test_404_model_skips_to_next_entry(self):
        r, calls = make(lambda u, k, m: Resp(404, "model not found") if m == "g-small" else Resp())
        res = r.complete("fast", MSG)
        self.assertEqual(res.provider, "gemini")
        self.assertEqual(sum(1 for c in calls if c[2] == "g-small"), 1)  # not retried on other keys

    def test_auth_cools_key_long(self):
        r, _ = make(lambda u, k, m: Resp(401, "bad key") if k == "gk1111" else Resp())
        r.complete("fast", MSG)
        r.clock.t += 1
        r.complete("fast", MSG)
        bad = [k for k in r.status()["providers"]["groq"]["keys"] if k["last_error"].startswith("auth")][0]
        self.assertGreater(bad["cooling_for_s"], 3600)

    def test_timeout_cools_key_and_continues(self):
        r, _ = make(lambda u, k, m: requests.Timeout() if k == "gk1111" else Resp())
        self.assertEqual(r.complete("fast", MSG).provider, "groq")

    def test_circuit_breaker_opens(self):
        r, _ = make(lambda u, k, m: Resp(500, "boom") if "groq" in u else Resp(),
                    groq=tuple(f"k{i}000" for i in range(8)))
        for _ in range(3):
            r.clock.t += 1
            r.complete("fast", MSG)
        self.assertEqual(r.status()["providers"]["groq"]["breaker"], "open")

    def test_keys_never_in_status_or_errors(self):
        secret = "SECRET-KEY-VALUE-9999"
        r, _ = make(lambda u, k, m: Resp(500, f"oops {k}"), groq=(secret,), gem=(), orr=())
        with self.assertRaises(AllBusy) as cm:
            r.complete("fast", MSG)
        blob = json.dumps(r.status(["vision"])) + str(cm.exception.errors)
        self.assertNotIn(secret, blob)

    def test_parse_keys_formats(self):
        self.assertEqual(parse_keys('["a","b"]', "groq"), [("a", "groq-1"), ("b", "groq-2")])
        self.assertEqual(parse_keys("a, b", "groq")[1][0], "b")
        self.assertEqual(parse_keys('[{"key":"a","group":"mail1"}]', "groq"), [("a", "mail1")])
        self.assertEqual(parse_keys("", "groq"), [])


class FakeRouter:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def tier_for(self, c):
        return "fast"

    def complete(self, tier, messages, **kw):
        self.calls.append(tier)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return Result(r, "groq", "m", "…1234", 1, tier)

    def status(self, chars=()):
        return {"ok": True}


class AppTests(unittest.TestCase):
    def client(self, replies, token="tok"):
        fr = FakeRouter(replies)
        return create_app(router=fr, personas=load_personas(), token=token).test_client(), fr

    H = {"X-App-Token": "tok"}

    def test_auth(self):
        c, _ = self.client(["नमस्ते"])
        self.assertEqual(c.post("/chat", json={"message": "hi"}).status_code, 401)
        self.assertEqual(c.get("/health").status_code, 200)
        c2, _ = self.client(["x"], token="")
        self.assertEqual(c2.post("/chat", json={"message": "hi"}, headers=self.H).status_code, 503)

    def test_hindi_chat(self):
        c, _ = self.client(["जी सर, बताइए क्या करना है?"])
        r = c.post("/chat", json={"character": "jarvis", "message": "hello"}, headers=self.H).get_json()
        self.assertEqual(r["speech"], "जी सर, बताइए क्या करना है?")
        self.assertTrue(r["language_ok"] and not r["degraded"] and r["tool_calls"] == [])

    def test_language_guard_rewrites_once(self):
        c, fr = self.client(["Sure sir, how can I help you today with that?", "जी सर, बताइए।"])
        r = c.post("/chat", json={"character": "jarvis", "message": "hello"}, headers=self.H).get_json()
        self.assertEqual(r["speech"], "जी सर, बताइए।")
        self.assertEqual(fr.calls, ["fast", "smart"])

    def test_degraded_is_in_character_not_error(self):
        c, _ = self.client([AllBusy(30, ["x"])])
        r = c.post("/chat", json={"character": "vision", "message": "hi"}, headers=self.H)
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(j["degraded"] and j["retry_after_s"] == 30 and "व्यस्त" in j["speech"])

    def test_bad_input(self):
        c, _ = self.client(["x"])
        self.assertEqual(c.post("/chat", json={"character": "nobody", "message": "hi"}, headers=self.H).status_code, 404)
        self.assertEqual(c.post("/chat", json={"message": "  "}, headers=self.H).status_code, 400)


class PersonaTests(unittest.TestCase):
    def test_personas_complete_and_hindi(self):
        ps = load_personas()
        self.assertEqual(set(ps), {"jarvis", "vision"})
        for p in ps.values():
            self.assertGreaterEqual(len(p["examples"]), 6)
            for ex in p["examples"]:
                self.assertGreater(hindi_ratio(ex["reply"]), 0.8, ex["reply"])
            self.assertIn("Devanagari", build_system_prompt(p))
            self.assertTrue(language_ok(p["busy_line"]))

    def test_language_guard(self):
        self.assertFalse(language_ok("Sure sir, I will do that right now for you"))
        self.assertTrue(language_ok("जी सर, Chrome खोल रहा हूँ"))
        self.assertTrue(language_ok("ok"))


if __name__ == "__main__":
    unittest.main()
