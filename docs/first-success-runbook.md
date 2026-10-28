# Runbook: the E4 (phone hotspot) and E5 (school VPN) profiles

You only have to run one of these to fill its slot; both is better. Budget 15 minutes each.
Nothing here needs admin rights, and nothing writes to the repository.

Two rules that decide whether the numbers mean anything:

1. **Never add flags.** No `-o`, no `--rate`, no `--only`. The default output directory is part
   of what is being measured.
2. **Do not fix anything mid-run.** If it fails, let it fail, note the row, move on.

Phase A/B and E are identical for both slots; only Phase C (E4) or Phase D (E5) differs.

---

## Phase A — install once, and record what you installed

This measures what a stranger actually gets, so it must be the real install path, not `-e .`.

```bash
cd /d/A/Harness/acadfetch
pipx install acadfetch          # if pipx is missing: python -m pip install --user pipx && python -m pipx ensurepath
acadfetch --version             # expect: acadfetch 0.1.1
acadfetch sources               # expect: five names
```

No pipx, no problem — any of these is equivalent for this purpose:

```bash
uvx --from acadfetch acadfetch --version          # uv
python -m venv .venv-bench && .venv-bench/Scripts/activate && pip install acadfetch
```

Write down which one you used. **A later re-run after the fix installs from git instead:**

```bash
pipx install --force "git+https://github.com/young-ski/acadfetch@<sha>"
```

## Phase B — check the resolver first

```bash
python -c "import socket; print(socket.gethostbyname('zenodo.org'))"
```

- Prints an IP → continue.
- Raises `getaddrinfo failed` or prints `0.0.0.0` → the resolver is blackholing it (it does on this
  machine). Add one line to `C:\Windows\System32\drivers\etc\hosts` **as administrator**:

  ```
  188.185.42.113 zenodo.org # acadfetch first-success bench
  ```

  then note `dns_pin=1` in the notes. Re-run the check; it must print that IP.

## Phase C — only for E4: make the hotspot the only route

A phone hotspot rides on Wi-Fi, so the Wi-Fi adapter stays up — what must go is every *other*
egress, plus the campus SSID's habit of auto-reconnecting.

```powershell
Get-NetAdapter | Where-Object Status -eq Up | Select-Object Name, InterfaceDescription
netsh wlan show interfaces
```

- `Wi-Fi` with your hotspot SSID -> Wi-Fi tethering, path A.
- `Remote NDIS based Internet Sharing Device` -> USB tethering, path B (cleaner isolation).

**Path A - Wi-Fi tethering.** One radio holds one SSID, so joining the hotspot already displaced
campus Wi-Fi. Disable the other egresses and stop the auto-reconnect:

```powershell
# elevated; use the names from Get-NetAdapter
Disable-NetAdapter -Name "<ethernet>" -Confirm:$false
Disable-NetAdapter -Name "<second wireless, if any>" -Confirm:$false
netsh wlan show profiles                                        # find the campus SSID
netsh wlan set profileparameter name="<campus-ssid>" connectionmode=manual
"tether=wifi" | Tee-Object "$E
otes.md" -Append
netsh wlan show interfaces | Tee-Object "$E
otes.md" -Append
```

**Path B - USB tethering.** Plug the cable, enable USB tethering, then disable both radios:

```powershell
Disable-NetAdapter -Name "Wi-Fi" -Confirm:$false
Disable-NetAdapter -Name "<ethernet>" -Confirm:$false
"tether=usb" | Tee-Object "$E
otes.md" -Append
```

Either way, confirm the egress actually moved:

```powershell
curl.exe -s https://api.ipify.org 2>&1 | Tee-Object "$E
otes.md" -Append
```

A carrier-looking IP different from the campus one -> continue. Unchanged -> another adapter still
holds the route; go back one step.

Restore in Phase G:

```powershell
Enable-NetAdapter -Name "<ethernet>" -Confirm:$false
netsh wlan set profileparameter name="<campus-ssid>" connectionmode=auto
```

## Phase D — only for E5: four checks before you trust the VPN

```bash
curl -s https://api.ipify.org && echo     # 1. egress IP before
```

Connect the VPN, then:

```bash
curl -s https://api.ipify.org && echo     # 1. egress IP after — MUST differ
curl -s https://ipinfo.io/json            # 2. read .org — must NOT be the campus/home ISP
python -c "import ssl; print(ssl.get_default_verify_paths())"   # 3. note whether you installed a CA
```

- Check 1 fails (same IP) → the VPN is split-tunnel: switch to full/global mode, or record
  `vpn=split` and treat this slot as route diversity only.
- Check 2 shows the same ISP → same as above, record `vpn=same-asn`.
- Check 3: if the VPN installs a private root CA, note `mitm=1`. A `CERTIFICATE_VERIFY_FAILED`
  result is a valid finding, not a crash — record it and continue.
- 4. Keep the session alive for all three attempts. If it drops, record `session_reset=1`, reconnect,
  and restart the three attempts from 1.

## Phase E — three attempts (both slots)

Replace `E4-hotspot` with `E5-vpn` for the other slot. Keep the directory after the run.

```bash
cd /d/A/Harness/acadfetch
python scripts/first_success.py \
  --env-id E4-hotspot \
  --installed \
  --attempts 3 \
  --timeout 600 \
  --gap 300 \
  --keep \
  --csv ".tmp/first-success-$(date +%Y%m%d-%H%M).csv"
```

`--gap 300` = five minutes between attempts, so one lucky minute does not define the result.
The script prints one line per attempt and appends a CSV row: exit code, seconds, bytes, whether a
`.part` survived, and the `verify` exit code. A row only counts as a pass when the exit code **and**
`verify` are both 0.

If an attempt hangs, the script kills it at `--timeout` and records `timeout>600s` — leave it.

## Phase F — send back these three lines plus notes

From the CSV the script wrote:

```
env, date, os, python, tool_ref, record, attempt, exit, elapsed_s, bytes, part_kept, verify, http_status, error
```

Fill in and paste, one row per attempt:

```
E4 / hotspo.. , 2026-11-.., Windows 11, 3.12, installed:acadfetch 0.1.1, 10.5281/zenodo.23612969, 1, <exit>, <s>, <bytes>, <0|1>, <verify>, <403/416/…>, <first error line>
```

Plus, in prose: `install_method`, `dns_pin`, and for E5 `vpn=full|split`, `vpn=<asn-org>`, `mitm=0|1`,
`session_reset=0|1`. Also say whether anything looked weird (a 20-minute silence, a captive-portal
page, an antivirus popup).

## Phase G — clean up

```bash
pipx uninstall acadfetch                  # only the E5 slot if E4 runs on another machine
ipconfig /flushdns
```

E4: re-enable Wi-Fi/Ethernet, and set the campus SSID back to `connectionmode=auto`. Remove the
`hosts` line if you added it and are done measuring.

Delete nothing under `.tmp/`. The attempt directories are the evidence, and with `--keep` they land
in a sibling folder named `.tmp/afbench-<env-id>-*`, not inside the evidence folder — copy them in
before you finish:

```powershell
$A = (Get-ChildItem .tmp -Directory -Filter 'afbench-<env-id>-*' | Select-Object -First 1).FullName
"attempt_root=$A" | Tee-Object "$E/notes.md" -Append
Get-ChildItem $A -Recurse -File | ForEach-Object { "$($_.FullName)  $($_.Length)" } | Tee-Object "$E/files.txt" -Append
Copy-Item "$A/*" $E -Recurse -Force
```

The failed attempt's folder is the one that must show no `.part`: that is the loss of progress, on
disk, for the record.

---

## Judgement (mine, do not pre-empt it)

An environment is green when at least 2 of 3 attempts pass and **no passing attempt later fails
`verify`**. Because a failed attempt has no manifest, `verify` on it is 1 by construction — it is
just a failure, not a separate strike.

An environment is *green with the target defect* when the 2/3 margin is met but a failure carries
`cannot read from timed out object` and `part_kept=0`. Those environments must be 3/3 after the fix;
the 2/3 margin only applies to fresh, not-yet-fixed baseline runs.

The 2-week gate needs 4 of 5 environments green **at 3/3**, plus the E2 stall profile at 100% with
`.part` retention at 100%. A figshare 403 on any single environment is not an environment failure —
it goes into the 6-week figshare review instead.
