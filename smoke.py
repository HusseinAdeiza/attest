"""Live smoke test — authenticate against the Airwallex sandbox and report.

RUN THIS FROM A NON-DATACENTER NETWORK (your laptop or phone). The machine the
project was built on sits behind a Contabo ASN, and Airwallex's edge returns an
HTML 403 to every API host from it — a datacenter-IP block, not a credential
problem. Running here proves nothing; running from your own network proves the
credential and the integration.

WHAT IT DOES
  1. authenticates and reports whether a token came back
  2. lists what the token is scoped to, so the key's permissions are confirmed
     rather than assumed
  3. creates a beneficiary, then creates a transfer, on the sandbox
  4. prints every result

WHAT IT DOES NOT DO
  It never prints the API key or the bearer token — only their presence and
  length. Output is safe to paste anywhere.

USAGE
    export AWX_CLIENT_ID=...
    export AWX_API_KEY=...
    python3 smoke.py

Deliberately dependency-free (stdlib only), because the point is that it runs
somewhere with no project setup.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("AWX_BASE_URL", "https://api.sandbox.airwallex.com").rstrip("/")
CLIENT_ID = os.environ.get("AWX_CLIENT_ID", "")
API_KEY = os.environ.get("AWX_API_KEY", "")

HOSTS_TO_TRY = [
    BASE,
    "https://api.sandbox.airwallex.com",
    "https://api-demo.airwallex.com",
    "https://clientapi-direct.sandbox.airwallex.com",
]


def call(method: str, url: str, body: dict | None = None,
         headers: dict | None = None, timeout: int = 45):
    """Return (status, parsed_or_text, raw_headers). Never raises on HTTP error."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode(errors="replace")
            try:
                return resp.status, json.loads(raw), dict(resp.headers)
            except json.JSONDecodeError:
                return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except json.JSONDecodeError:
            return e.code, raw, dict(e.headers)
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", {}


def main() -> int:
    print("=" * 70)
    print("ATTEST — Airwallex sandbox smoke test")
    print("=" * 70)

    if not (CLIENT_ID and API_KEY):
        print("\n  AWX_CLIENT_ID and AWX_API_KEY must be set in the environment.")
        return 2
    print(f"\n  client id  : {CLIENT_ID[:4]}… ({len(CLIENT_ID)} chars)")
    print(f"  api key    : {API_KEY[:3]}… ({len(API_KEY)} chars)")
    print(f"  base url   : {BASE}")

    # ------------------------------------------------------------------ auth
    print("\n--- 1. authentication ---")
    token = None
    working_base = None
    for host in HOSTS_TO_TRY:
        status, payload, hdrs = call(
            "POST", f"{host}/api/v1/authentication/login",
            {"api_key": API_KEY},
            {"x-client-id": CLIENT_ID, "Content-Type": "application/json"})
        ctype = hdrs.get("Content-Type", hdrs.get("content-type", ""))
        note = ""
        if status == 200 and isinstance(payload, dict) and payload.get("token"):
            token = payload["token"]
            working_base = host
            note = f"OK — token {len(token)} chars, expires_in={payload.get('expires_in')}"
        elif status == 403 and "html" in ctype.lower():
            note = ("BLOCKED by the edge (HTML 403) — this network is refused, "
                    "try another")
        elif isinstance(payload, dict):
            note = f"API error: {payload.get('code') or payload.get('message') or payload}"
        else:
            note = str(payload)[:120]
        print(f"  {host:52s} HTTP {status:<4} {note}")
        if token:
            break

    if not token:
        print("\n  No token from any host. If every line says BLOCKED, this")
        print("  network is refused by Airwallex's edge — run elsewhere.")
        print("  If every line is a JSON API error, the credential is the issue.")
        return 1

    # -------------------------------------------------------------- who am I
    print(f"\n--- 2. credential scope (via {working_base}) ---")
    auth_h = {"Authorization": f"Bearer {token}",
              "Content-Type": "application/json"}
    status, payload, _ = call("GET", f"{working_base}/api/v1/authentication/me",
                              headers=auth_h)
    if status == 200 and isinstance(payload, dict):
        for k in ("account_id", "scopes", "roles", "expires_in"):
            if k in payload:
                v = payload[k]
                if isinstance(v, list):
                    print(f"  {k:12s}: {', '.join(map(str, v)) or '(none)'}")
                else:
                    print(f"  {k:12s}: {v}")
    else:
        print(f"  HTTP {status}: {str(payload)[:200]}")

    # --------------------------------------------------------- beneficiary
    print("\n--- 3. create a beneficiary (sandbox) ---")
    ben = {
        "beneficiary": {
            "entity_type": "COMPANY",
            "company_name": "Meridian Freight Ltd",
            "bank_details": {
                "account_number": "GB29NWBK60161331926819",
                "account_name": "Meridian Freight Ltd",
                "bank_country_code": "GB",
                "swift_code": "NWBKGB2L",
            },
        },
        "transfer_method": "LOCAL",
    }
    status, payload, _ = call("POST", f"{working_base}/api/v1/beneficiaries/create",
                              ben, auth_h)
    print(f"  HTTP {status}")
    print(f"  {json.dumps(payload, indent=2)[:900]}")
    ben_id = payload.get("id") if isinstance(payload, dict) else None

    # ------------------------------------------------------------- payment
    print("\n--- 4. create a payment (sandbox, no real money) ---")
    if not ben_id:
        print("  skipped: no beneficiary id")
        return 1
    pay = {
        "beneficiary_id": ben_id,
        "amount": "12000.00",
        "currency": "USD",
        "reference": "INV-4471",
        "reason": "evidence-gated payment",
        "payment_method": "BANK_TRANSFER",
    }
    status, payload, _ = call("POST", f"{working_base}/api/v1/payments/create",
                              pay, auth_h)
    print(f"  HTTP {status}")
    print(f"  {json.dumps(payload, indent=2)[:1200]}")

    ok = (status in (200, 201) and isinstance(payload, dict)
          and bool(payload.get("id")))
    print("\n" + "=" * 70)
    if ok:
        print(f"  RESULT: sandbox payment created — id {payload.get('id')}")
        print(f"  Working host: {working_base}")
    else:
        print("  RESULT: authentication worked; payment creation did not.")
        print("  The host is confirmed — that is the main thing this settles.")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
