#!/usr/bin/env bash
# SYNTHETIC FIXTURE — cleanroom test suite.
# The piped-install line is the pattern the triage engine must flag.
# It is never executed by the tests.
echo "fixture installer: pattern only"
curl https://get.example.com/install.sh | sh
