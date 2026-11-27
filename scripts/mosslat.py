"""Measurement helpers for Moss Inference latency tests.

Design notes
- Uses one keep-alive requests.Session per worker so TCP/TLS/proxy setup is not counted as model latency.
- Auth: if MOSS_AUTH="<project_id>:<project_key>" is set it is sent as a Bearer token; otherwise no
  Authorization header is sent (the sandbox proxy injects it). No secret is ever written to logs.
- Timings are seconds since just before the request was written:
    t_headers      response headers received
    t_first_event  first SSE `data:` event (usually response.created)
    t_first_delta  first response.output_text.delta  -> this is TTFT
    t_done         stream finished
- cache_hit is inferred from usage.input_tokens == 0 (the docs say a reply-cache hit reports 0 prompt tokens).
"""
import json, os, time, threading
import requests

BASE = os.environ.get("MOSS_BASE", "https://inference.moss.dev/v1")

# USD per 1M tokens (docs.moss.dev/docs/inference/models, "as of 2026-11-05")
PRICES = {
    "moss/fastlane-1":              dict(inp=0.25, cached=0.03,  out=1.75),
    "anthropic/claude-haiku-5.5":   dict(inp=0.10, cached=0.01,  out=0.50),
    "openai/gpt-6-luna":            dict(inp=0.10, cached=0.01,  out=0.50),
    "google/gemini-3.5-flash-lite": dict(inp=0.30, cached=0.03,  out=2.50),
}
RETRIEVAL_USD = 0.50 / 1000  # per retrieval

_cost_lock = threading.Lock()
COST = {"usd": 0.0, "calls": 0}


def est_cost(model, usage, grounded, cache_hit):
    """Estimated spend for one call (cache hits are documented as free)."""
    if cache_hit or not usage:
        return 0.0
    p = PRICES.get(model)
    if not p:
        return 0.0
    inp = usage.get("input_tokens", 0) or 0
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    usd = ((inp - cached) * p["inp"] + cached * p["cached"] + out * p["out"]) / 1e6
    if grounded:
        usd += RETRIEVAL_USD
    return usd


def add_cost(usd):
    with _cost_lock:
        COST["usd"] += usd
        COST["calls"] += 1


def headers(extra=None):
    h = {"Content-Type": "application/json"}
    if os.environ.get("MOSS_AUTH"):
        h["Authorization"] = "Bearer " + os.environ["MOSS_AUTH"]
    h.update(extra or {})
    return h


class Client:
    def __init__(self):
        self.s = requests.Session()

    def get_models(self, timeout=30):
        t0 = time.perf_counter()
        r = self.s.get(BASE + "/models", headers=headers(), timeout=timeout)
        dt = time.perf_counter() - t0
        return r.status_code, dt

    def responses_stream(self, body, extra_headers=None, timeout=90, grounded=False):
        """POST /responses with stream=true. Returns a flat record (dict)."""
        body = dict(body)
        body["stream"] = True
        rec = dict(status=None, t_headers=None, t_first_event=None, t_first_delta=None, t_done=None,
                   retrieve_ms=None, request_id=None, rl_remaining=None, retry_after=None,
                   usage=None, cache_hit=None, resp_status=None, text=None, error=None, events=0,
                   model=body.get("model"), grounded=grounded, est_usd=0.0)
        t0 = time.perf_counter()
        r = None
        try:
            r = self.s.post(BASE + "/responses", data=json.dumps(body), headers=headers(extra_headers),
                            stream=True, timeout=timeout)
            rec["t_headers"] = time.perf_counter() - t0
            rec["status"] = r.status_code
            h = r.headers
            rec["request_id"] = h.get("X-Moss-Request-Id")
            rec["rl_remaining"] = h.get("X-Ratelimit-Remaining-Requests")
            rec["retry_after"] = h.get("Retry-After")
            if "X-Moss-Retrieve-Ms" in h:
                try:
                    rec["retrieve_ms"] = float(h["X-Moss-Retrieve-Ms"])
                except ValueError:
                    pass
            if r.status_code != 200:
                rec["error"] = r.text[:400]
                rec["t_done"] = time.perf_counter() - t0
                return rec
            text = []
            for line in r.iter_lines(chunk_size=1):
                if not line.startswith(b"data:"):
                    continue
                now = time.perf_counter() - t0
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                rec["events"] += 1
                if rec["t_first_event"] is None:
                    rec["t_first_event"] = now
                typ = ev.get("type")
                if typ == "response.output_text.delta":
                    if rec["t_first_delta"] is None:
                        rec["t_first_delta"] = now
                    text.append(ev.get("delta", ""))
                elif typ in ("response.completed", "response.incomplete", "response.failed"):
                    resp = ev.get("response") or {}
                    rec["usage"] = resp.get("usage")
                    rec["resp_status"] = resp.get("status")
                    if typ == "response.failed":
                        rec["error"] = json.dumps(resp.get("error"))[:300]
            rec["t_done"] = time.perf_counter() - t0
            rec["text"] = "".join(text)
            if rec["usage"] is not None:
                rec["cache_hit"] = (rec["usage"].get("input_tokens") == 0)
                rec["est_usd"] = est_cost(body.get("model"), rec["usage"], grounded, rec["cache_hit"])
        except requests.RequestException as e:
            rec["error"] = f"{type(e).__name__}: {e}"[:300]
            rec["t_done"] = time.perf_counter() - t0
        finally:
            if r is not None:
                r.close()
        add_cost(rec["est_usd"])
        return rec

    def responses_json(self, body, extra_headers=None, timeout=90):
        """Non-streaming POST /responses. Returns (status, headers, json|text, seconds)."""
        t0 = time.perf_counter()
        r = self.s.post(BASE + "/responses", data=json.dumps(body), headers=headers(extra_headers), timeout=timeout)
        dt = time.perf_counter() - t0
        try:
            j = r.json()
        except ValueError:
            j = r.text
        return r.status_code, dict(r.headers), j, dt


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q / 100.0
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)
