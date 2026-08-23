#!/usr/bin/env python3
"""Report one run of one process into Process_Runs. Stdlib only, and it never fails a job.

The collector already reads every run out of GitHub Actions and the two scheduled jobs on the
Mac, so nothing that runs in those two places needs this. This is for everything else: a cloud
routine, or an engine that used to need a person in a terminal and now runs on its own. Those
report nowhere, so they have to say so themselves.

The contract that matters: a heartbeat must never be the reason a run fails. Every failure path
here prints one line and exits 0.

From a shell, wrapping whatever the engine does:

    python3 heartbeat.py --process fanish-carousel --status ok --duration 42 \
        --notes "3 decks built, 1 parked for review"

From inside a script, which is what a converted engine should do:

    from heartbeat import beat, report
    with report("fanish-carousel") as run:          # times it, and reports fail on a crash
        ...
        run.note("3 decks built")

Credentials come from AIRTABLE_API_KEY / AIRTABLE_BASE_ID, or OPS_AIRTABLE_* when a repo has to
carry two different keys, or a local .env next to this file. With no credentials it prints
"no key, skipped" and exits 0, so a workflow can carry the step before the secret exists.
"""
import os
import sys
import json
import time
import argparse
import datetime as dt
import contextlib
import urllib.request
import urllib.error

TABLE = "Process_Runs"
STATUSES = ("ok", "fail", "blocked", "skipped")
TIMEOUT = 15

try:
    import ops_common as oc
except ImportError:                       # curl-and-pipe use: no repo checkout, no sibling module
    oc = None


def _creds():
    if oc is not None:
        return oc.creds()
    for a, b in (("OPS_AIRTABLE_API_KEY", "OPS_AIRTABLE_BASE_ID"),
                 ("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID")):
        if os.environ.get(a) and os.environ.get(b):
            return os.environ[a].strip(), os.environ[b].strip()
    return None, None


def beat(process, status="ok", duration=None, notes="", docs=None, source=None):
    """Write one Process_Runs row. Returns True if it landed. Never raises."""
    try:
        if status not in STATUSES:
            status = "fail"
        key, base = _creds()
        if not key or not base:
            print(f"heartbeat {process}: no key, skipped")
            return False
        note = (notes or "").strip()
        if source:
            note = f"{note} · src={source}" if note else f"src={source}"
        fields = {
            "Process": process,
            "Ran_At": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "Status": status,
        }
        if duration is not None:
            fields["Duration_Sec"] = int(duration)
        if note:
            fields["Notes"] = note[:255]
        if docs:
            fields["Docs_Link"] = docs
        payload = json.dumps({"records": [{"fields": fields}], "typecast": True}).encode()
        req = urllib.request.Request(
            f"https://api.airtable.com/v0/{base}/{TABLE}", data=payload, method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
        print(f"heartbeat {process}: {status}" + (f" in {int(duration)}s" if duration else ""))
        return True
    except Exception as e:                # a heartbeat must never fail the job it is reporting on
        print(f"heartbeat {process}: not recorded ({type(e).__name__}: {e})")
        return False


class _Run:
    def __init__(self, process):
        self.process = process
        self.notes = []
        self.status = "ok"

    def note(self, text):
        self.notes.append(str(text))

    def blocked(self, why):
        """The engine could not proceed and is waiting on something, usually Lazar."""
        self.status = "blocked"
        self.note(why)

    def skipped(self, why):
        """Nothing to do this run, which is a healthy outcome, not a failure."""
        self.status = "skipped"
        self.note(why)


@contextlib.contextmanager
def report(process, docs=None):
    """Time a run and report it, whatever happens. An exception reports fail and re-raises."""
    run, started = _Run(process), time.time()
    try:
        yield run
    except BaseException as e:
        beat(process, "fail", time.time() - started,
             f"{type(e).__name__}: {e}"[:200], docs)
        raise
    beat(process, run.status, time.time() - started, "; ".join(run.notes), docs)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--process", required=True)
    p.add_argument("--status", default="ok", choices=list(STATUSES) + ["success", "failure",
                                                                      "cancelled"])
    p.add_argument("--duration", type=float, default=None)
    p.add_argument("--notes", default="")
    p.add_argument("--docs", default=None)
    p.add_argument("--source", default=None,
                   help="an id that makes this run unique, so a re-report does not duplicate it")
    a = p.parse_args()
    # GitHub hands job.status through as success/failure/cancelled; accept it as-is.
    status = {"success": "ok", "failure": "fail", "cancelled": "skipped"}.get(a.status, a.status)
    beat(a.process, status, a.duration, a.notes, a.docs, a.source)
    sys.exit(0)                            # always, by contract


if __name__ == "__main__":
    main()
