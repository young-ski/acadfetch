# Documentation

Everything here is written for someone using `acadfetch` or picking it up to
change it. The last group is process history: it records how decisions were
made, not how the tool behaves today.

## Using it

- [demo.md](demo.md) — a real 30-second transcript: dry run, fetch, verify.
- [integrations.md](integrations.md) — aria2, Make, GitHub Actions, Snakemake, and
  a pooch downloader.
- [troubleshooting.md](troubleshooting.md) — arranged by what you are looking at,
  not by cause.
- [manifest.md](manifest.md) — every field and state of `manifest.json`, and what
  `verify` does and does not prove.
- [json-report.md](json-report.md) — the `--json` contract, field by field.

## Moving off figshare

- [migration-0.2.md](migration-0.2.md) — the adapter is gone; here is the
  conversion, and why an existing manifest still verifies.

## Maintaining it

- [releasing.md](releasing.md) — the checklist to follow at 23:00.
- [first-success-runbook.md](first-success-runbook.md) — the E1–E5 network
  profiles behind the first-success bench.
- [e5-vpn-runbook.md](e5-vpn-runbook.md) — the VPN profile on its own.

## Process history

Kept because the reasoning is worth more than the conclusions. None of these
describe current behaviour, and a plan is not a promise.

- [OPTIMIZATION-PLAN.md](OPTIMIZATION-PLAN.md) — the 0.1.1 audit that set the
  0.2.0 direction, including the measurements behind it.
- [plan-0.2.0.md](plan-0.2.0.md) — the 0.2.0 execution plan, including what was
  deliberately rejected.
- [py315-windows-report.md](py315-windows-report.md) — how the Python 3.15 claim
  was reached, and what it did not prove.
