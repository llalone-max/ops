#!/usr/bin/env python3
"""Shared helpers for the ops pages. Stdlib only, and safe to read: this repo is PUBLIC.

  1. Airtable read and write against the Ops base, with credentials from env or a local .env.
  2. public_text(): the gate every line of Lazar-facing prose passes before it reaches a page
     that is committed into this public repo.

The process table itself (names, triggers, docs links) lives in the PRIVATE ops repo on the
Mac as process_spec.py, because those names spell out the brands.
"""
import os
import re
import json
import urllib.parse
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))

# Where a local run may find AIRTABLE_API_KEY / AIRTABLE_BASE_ID / BRAND_MAP. First hit wins.
# `.env` here is a symlink to the private keys folder on the Mac; in CI these arrive as env vars.
ENV_CANDIDATES = tuple(p for p in (os.path.join(HERE, ".env"), os.environ.get("OPS_ENV_FILE")) if p)

# ----------------------------------------------------------- what may appear on the public page

# The `ops` repo is PUBLIC and dashboard.html is committed into it, so anything rendered is
# world-readable and stays in git history. Open_Items text is written for Lazar, not for the
# world, so every row passes this gate before a word of it reaches the page.
#
# Two steps. First MASK what can be masked safely: brand names to their abbreviation, a
# collaborator's first name to an initial, a domain to a placeholder. Then HOLD BACK the whole
# row if what is left still names a credential, a path or an address. A held-back row still
# shows on the page as a counted item with its engine, kind and age; only its text is withheld.

# First names of real people who appear in the items. Masked to an initial before publishing.
PEOPLE = ("Ilya", "Lucie", "Maribel", "John", "Lazar")

# If any of these survive the masking pass, the row's text is held back.
UNSAFE_PATTERNS = (
    r"(?i)\b(api[_ -]?key|secret|token|credential|password|service[_ -]?account|passphrase)\b",
    r"(?i)\brotat(e|ing|ion)\b",
    r"(?i)\bpurge\b",
    r"(?i)\b[A-Z][A-Z0-9_]{5,}_(KEY|TOKEN|SECRET|JSON)\b",
    r"\bpat[A-Za-z0-9]{6,}\b",
    r"[\w.+-]+@[\w-]+\.[\w.]+",           # an email address
    r"(?:^|\s)(?:~/|/Users/|/home/)\S+",  # a path on someone's machine
)


def public_text(text, brand_map):
    """(masked text, held_back). held_back True means show the row but not its words."""
    if not text:
        return "", False
    s = str(text)
    for full, ab in (brand_map or {}).items():
        words = [re.escape(w) for w in re.split(r"[ _-]+", full) if w]
        if words:
            s = re.sub("(?i)" + r"[-_ ]?".join(words), ab, s)
    for name in PEOPLE:
        s = re.sub(r"\b" + re.escape(name) + r"(?:'s)?\b", name[0] + ".", s)
    s = re.sub(r"\b[a-z0-9][a-z0-9-]{2,}\.(?:com|net|org|io|co|ai|dev)\b", "a client site", s)
    for pat in UNSAFE_PATTERNS:
        if re.search(pat, s):
            return "", True
    return s, False


# ----------------------------------------------------------------------------- credentials


def load_env():
    """Env vars win; otherwise the first .env in ENV_CANDIDATES that has the key."""
    d = {}
    for path in ENV_CANDIDATES:
        if not os.path.exists(path):
            continue
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k in ("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID", "BRAND_MAP",
              "OPS_AIRTABLE_API_KEY", "OPS_AIRTABLE_BASE_ID"):
        if os.environ.get(k):
            d[k] = os.environ[k]
    if d.get("OPS_AIRTABLE_API_KEY"):
        d["AIRTABLE_API_KEY"] = d["OPS_AIRTABLE_API_KEY"]
    if d.get("OPS_AIRTABLE_BASE_ID"):
        d["AIRTABLE_BASE_ID"] = d["OPS_AIRTABLE_BASE_ID"]
    return d


def creds():
    """(key, base) for the Ops base, or (None, None) when nothing is configured."""
    e = load_env()
    k, b = e.get("AIRTABLE_API_KEY"), e.get("AIRTABLE_BASE_ID")
    return (k.strip() if k else None), (b.strip() if b else None)


# ----------------------------------------------------------------------------- Airtable


def _req(url, key, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {key}"}
    if data:
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    return json.loads(urllib.request.urlopen(r).read())


def fetch(table, key, base, params=None):
    """Every record in a table as [{'id':..., 'fields':{...}}, ...]."""
    rows, off = [], None
    while True:
        q = {"pageSize": "100"}
        if params:
            q.update(params)
        if off:
            q["offset"] = off
        url = f"https://api.airtable.com/v0/{base}/{urllib.parse.quote(table)}?" + urllib.parse.urlencode(q)
        d = _req(url, key)
        rows += d["records"]
        off = d.get("offset")
        if not off:
            return rows


def create(table, key, base, records, typecast=True):
    """records is a list of field dicts. Returns the created records. Batches of 10."""
    out = []
    url = f"https://api.airtable.com/v0/{base}/{urllib.parse.quote(table)}"
    for i in range(0, len(records), 10):
        chunk = [{"fields": f} for f in records[i:i + 10]]
        out += _req(url, key, "POST", {"records": chunk, "typecast": typecast})["records"]
    return out


def update(table, key, base, records, typecast=True):
    """records is a list of {'id':..., 'fields':{...}}. Batches of 10."""
    out = []
    url = f"https://api.airtable.com/v0/{base}/{urllib.parse.quote(table)}"
    for i in range(0, len(records), 10):
        out += _req(url, key, "PATCH", {"records": records[i:i + 10], "typecast": typecast})["records"]
    return out
