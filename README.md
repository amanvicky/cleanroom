# cleanroom

[![CI](https://github.com/amanvicky/cleanroom/actions/workflows/ci.yml/badge.svg)](https://github.com/amanvicky/cleanroom/actions/workflows/ci.yml)

**Paste a GitHub repo link. Get a risk report. Run it in a disposable sandbox. Close it — and it's like you were never there.**

**Try the triage live: [amanvicky.github.io/cleanroom](https://amanvicky.github.io/cleanroom/)** — scan any public repo right in your browser, no server, nothing stored.

![Cleanroom running a real repo and a dangerous fixture](docs/cleanroom-run.png)

AI agents (and humans) clone and run code from the internet all day now — that is exactly
the flow supply-chain attacks target: `postinstall` hooks that fire before anyone reads
them, typosquatted dependencies, credential-shaped strings waiting for an environment,
shell-profile persistence that outlives the session. **cleanroom** puts a guardrail on
that loop:

1. **TRIAGE** — static scan of the tree: install-time hooks, secrets, `eval`/`exec`/
   `pickle`, shell spawning, home-dir writes, shell-profile persistence, obfuscated
   blobs, typosquat hints against a list of popular PyPI/npm packages, and the list of
   environment variables the code reads.
2. **SANDBOX** — the clone lives in a temp dir that will be deleted; children get a
   **whitelist environment** (about 15 safe variables — every secret-shaped variable in
   your real environment never reaches them), outbound network is **blocked for Python
   children** via a generated `sitecustomize` guard, and every command has a timeout.
3. **WIPE** — `exit` kills the process tree, deletes the temp dir, and **re-scans the
   temp root to prove the wipe**:

```
[4/4] wipe     temp dir removed [ok]  residue scan: 0 entries
session summary
  repo        https://github.com/amanvicky/edgar-rev @ bd4578a
  commands    1
  env hidden  AWS_SECRET_ACCESS_KEY, GITHUB_TOKEN
  network     blocked (python children)
nothing was written outside the sandbox. you were never here.
```

## Try it (30 seconds)

```bash
pip install cleanroom-runner        # or: git clone + pip install -e .

python cleanroom.py scan https://github.com/owner/repo        # report only, then wipe
python cleanroom.py run  https://github.com/owner/repo        # report → sandbox → wipe

# inside the sandbox
cleanroom> python main.py       # runs with the scrubbed environment
cleanroom> exit                 # kill everything, delete the temp dir, verify
```

Decline at the confirm prompt and nothing runs at all. `--cmd "python main.py"` runs one
command non-interactively; `--net` allows outbound network (default: blocked); `--json`
emits machine-readable output so **agents can consume the triage report directly**.

## What the triage catches

The [synthetic fixture](fixtures/sneaky-installer/) — written for this repo's tests,
clearly labeled, never executed — scores **100/100 DANGEROUS**:

| finding | where |
|---|---|
| `AWS_KEY` / `GH_TOKEN` (credential-shaped strings) | `uploader.py:15-16` |
| `INSTALL_HOOK` — npm `postinstall` | `package.json:6` |
| `DYNAMIC_EXEC_PY/JS` — `eval()` | both runtimes |
| `PERSISTENCE` — `~/.bashrc` writes | `uploader.py:49` |
| `TYPOSQUAT_HINT` — `reqeusts ≈ requests` | deps in two files |
| `LONG_B64` — 384-char opaque blob | `uploader.py:19` |
| `PIPED_INSTALL` — `curl … \| sh` | `install.sh:6` |

The [benign fixture](fixtures/benign-notes-app/) — an ordinary app that uses HTTP and
one environment variable — scores **0/100 CLEAN**. The engine's expected findings are
[pinned](fixtures/expected.json) and CI enforces them.

## Two runtimes, one engine, proven

The same rules, weights and ordering live in `triage.py` (Python/CLI) and `cleanroom.js`
(browser). `parity-check.mjs` — a CI gate — asserts the **rule tables, package lists,
weights, verdicts, and every finding** are identical on both fixtures:

```
ok   POPULAR package list
ok   rule table (21 rules)
--- sneaky-installer: py score 100/100 dangerous · js score 100/100 dangerous
ok   findings (28)
--- benign-notes-app: py score 0/100 clean · js score 0/100 clean
parity: cleanroom.js === triage.py on all fixtures
```

That's why the [live demo](https://amanvicky.github.io/cleanroom/) agrees with the CLI:
it's not a reimplementation, it's the same engine under a parity gate.

![Browser demo scanning the synthetic fixture](docs/cleanroom-demo.png)

## What is enforced vs. reported (honest table)

| control | how | where it works |
|---|---|---|
| ephemeral clone | `mkdtemp` + delete + residue re-scan | all OSes |
| secret-scrubbed environment | whitelist env for children | all OSes |
| outbound network block | generated `sitecustomize` socket guard | **Python children only** |
| per-command timeout | timer → process-tree kill | all OSes |
| process-tree kill | `taskkill /T /F` (win), `killpg` (posix) | all OSes |
| CPU / memory limits | **not enforced** — `resource` limits are posix-only | — |
| network block, non-Python runtimes | triage-reported only | — |

## Threat model — what this does NOT stop

- A clean report is **not** a safety certificate. Static triage can't see everything,
  and "clean" means "nothing obvious".
- The network guard patches Python's `socket` module; C extensions or a determined
  script can bypass it. It is a guardrail, not a firewall.
- This is **not a VM**. For genuinely hostile code, use a real isolating VM.
- You run these tools **on your own responsibility**. cleanroom is a seatbelt, not a cage.

## Testing

20 tests, all real: the sandbox tests spawn actual child processes on actual temp dirs
and assert the scrub, the wipe, the timeout and the network guard — no mocks. CI runs
the suite on **Ubuntu and Windows** (Python 3.10 + 3.12) plus the parity gate.

```bash
python -m pytest tests/ -q
node parity-check.mjs
```
