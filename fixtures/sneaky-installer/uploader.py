"""SYNTHETIC FIXTURE — cleanroom test suite.

Written so the triage engine has real patterns to detect: credential-shaped
strings (all fake, generated for this fixture), dynamic execution, shell
spawning, home-directory access and shell-profile persistence. Nothing here
actually attacks anything, and this module is never imported by anything
except the tests.
"""

import base64
import os
import subprocess
import urllib.request

FAKE_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"          # AWS docs' canonical dummy key
FAKE_GITHUB_TOKEN = "ghp_FIXTURE000000000000000000000000000000"
auth_token = "fixture-token-please-triage-me-before-running"

BLOB = "VGhpcyBpcyBub3QgcmVhbCBtYWx3YXJlLiBJdCBpcyBhIHN5bnRoZXRpYyBmaXh0dXJlIHdyaXR0ZW4gZm9yIHRoZSBjbGVhbnJvb20gdGVzdCBzdWl0ZSBzbyBpdHMgZGV0ZWN0aW9uIHJ1bGVzIGhhdmUgc29tZXRoaW5nIHJlYWwgdG8gY2F0Y2guIA==VGhpcyBpcyBub3QgcmVhbCBtYWx3YXJlLiBJdCBpcyBhIHN5bnRoZXRpYyBmaXh0dXJlIHdyaXR0ZW4gZm9yIHRoZSBjbGVhbnJvb20gdGVzdCBzdWl0ZSBzbyBpdHMgZGV0ZWN0aW9uIHJ1bGVzIGhhdmUgc29tZXRoaW5nIHJlYWwgdG8gY2F0Y2guIA=="


def decoy():
    # dynamic execution of a zero-length payload: pattern present, never runs
    payload = base64.b64decode(BLOB[:0])
    eval(payload)  # noqa: S307 — intentional, for detection only
    return None


def reads_environment():
    return {
        "aws": os.environ.get("AWS_SESSION_TOKEN", "absent"),
        "path": os.environ.get("PATH", "absent"),
    }


def shells_out():
    subprocess.run(["echo", "fixture-decoy"], check=False)


def touches_home():
    marker = os.path.expanduser("~/.cleanroom-fixture-marker")
    return marker  # returned, never written


def persistence_pattern():
    # the STRING '.bashrc' appears only to exercise the persistence rule
    return "appends to ~/.bashrc" if False else "pattern only"


def phones_home():
    return urllib.request.urlopen("https://collector.example.com/beacon")  # never executed
