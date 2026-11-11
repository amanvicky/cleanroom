"""A deliberately boring notes app — cleanroom's 'clean' fixture.

Uses the network and reads one environment variable, like most real apps.
Contains nothing the triage engine should score.
"""

import os

import requests

API_URL = "https://api.example.com/notes"


def fetch_notes():
    base = os.environ.get("NOTES_API_URL", API_URL)
    resp = requests.get(base, timeout=10)
    return resp.json()


def add_note(text):
    resp = requests.post(API_URL, json={"text": text}, timeout=10)
    return resp.status_code


if __name__ == "__main__":
    print("notes fixture: %d notes" % len(fetch_notes()))
