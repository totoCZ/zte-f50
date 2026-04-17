#!/usr/bin/env python3
"""Login flow debugger — compare output against Safari Network tab."""

import requests
import hashlib
import os
import random
import string
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

HOST     = os.getenv("ZTE_HOST", "10.50.0.1")
USERNAME = os.getenv("ZTE_USER", "admin")
PASSWORD = os.getenv("ZTE_PASS", "")
BASE     = f"http://{HOST}"


def sha256(s): return hashlib.sha256(s.encode()).hexdigest().upper()
def md5(s):    return hashlib.md5(s.encode()).hexdigest()

def compute_password(plain, ld): return sha256(sha256(plain) + ld)
def compute_ad(cr, wa, rd):      return sha256(sha256(wa + cr) + rd)


def step(label, r):
    print(f"\n{'─'*60}")
    print(f"STEP: {label}")
    print(f"  {r.request.method} {r.request.url}")
    print(f"  Request headers: {dict(r.request.headers)}")
    if r.request.body:
        print(f"  Request body:    {r.request.body}")
    print(f"  Status: {r.status_code}")
    print(f"  Response cookies: {dict(r.cookies)}")
    try:
        print(f"  Response JSON:   {r.json()}")
    except Exception:
        print(f"  Response text:   {r.text[:300]}")


s = requests.Session()
s.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': f'{BASE}/index.html',
})

print(f"Target: {BASE}  user={USERNAME}")
print("Session cookies after each step shown — compare with Safari Network tab\n")

# ── Step 0: initial page load (hypothesis: required for proper session) ───────
r = s.get(f'{BASE}/index.html', timeout=10)
step("GET index.html", r)

# ── Step 1: fetch versions + LD + RD ─────────────────────────────────────────
r = s.get(f'{BASE}/goform/goform_get_cmd_process',
          params={'isTest': 'false', 'cmd': 'Language,cr_version,wa_inner_version', 'multi_data': '1'})
step("GET versions", r)
ver = r.json()
cr  = ver.get('cr_version', '')
wa  = ver.get('wa_inner_version', '')
print(f"  cr_version={cr!r}  wa_inner_version={wa!r}")

r = s.get(f'{BASE}/goform/goform_get_cmd_process', params={'isTest': 'false', 'cmd': 'LD'})
step("GET LD", r)
ld = r.json().get('LD', '')
print(f"  LD={ld!r}")

r = s.get(f'{BASE}/goform/goform_get_cmd_process', params={'isTest': 'false', 'cmd': 'RD'})
step("GET RD", r)
rd = r.json().get('RD', '')
print(f"  RD={rd!r}")

# ── Step 2: POST login ────────────────────────────────────────────────────────
pw = compute_password(PASSWORD, ld)
ad = compute_ad(cr, wa, rd)
print(f"\n  computed password={pw!r}")
print(f"  computed AD      ={ad!r}")

r = s.post(f'{BASE}/goform/goform_set_cmd_process', data={
    'isTest': 'false',
    'goformId': 'LOGIN',
    'password': pw,
    '_': str(int(time.time() * 1000)),
})
step("POST LOGIN", r)
resp = r.json()
print(f"\n  Full response: {resp}")
print(f"  loginfo={resp.get('loginfo')!r}  result={resp.get('result')!r}")
print("  loginfo='ok' => success")

# ── Step 3: try a data fetch ─────────────────────────────────────────────────
if result == '3':
    r = s.get(f'{BASE}/goform/goform_get_cmd_process',
              params={'isTest': 'false', 'cmd': 'network_type,lte_rsrp', 'multi_data': '1'})
    step("GET data (post-login)", r)
else:
    print("\n  Skipping data fetch — login did not return 3")

print(f"\n{'─'*60}")
print("Done. Compare each request above with Safari Network tab during a manual login.")
