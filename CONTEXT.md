# Context for whoever (human or AI) picks this project up next

This file is a handoff document. It assumes no memory of any prior
conversation about this repo — everything you need to not re-derive
decisions from scratch, or repeat mistakes already made once, should be here.
The README documents *what the project is and how to use it*; this file
documents *why it's built the way it is, what's been verified, and what
hasn't*.

---

## What this is, in one paragraph

A self-hosted NAS dashboard — file browser, on-demand SMB sharing, host
system monitoring — built as a CasaOS replacement for a NUC-as-NAS setup.
FastAPI + psutil backend, React SPA frontend, single Docker container. A
companion project, [smb-web-client](https://github.com/codemastervy/smb-web-client),
is a pure SMB *client* with a web UI, meant to connect to shares this
dashboard creates — the two are deliberately kept separate, never merged.

## How this was built and tested

Built and iterated inside an ephemeral Ubuntu 24.04 + Docker VM (via Lima on
macOS), used specifically because the failure modes this app cares about most
— container cgroup stats vs. host stats, SMB permission enforcement,
privileged device access — can't be verified any other way than running the
real thing on real Linux with real Docker. Every significant claim in the
README and in `TEST_RESULTS.md` was checked by actually running it: building
the image, starting the container, hitting the API, and in several cases
connecting from a **genuinely separate second device** (macOS's own SMB
client) to prove the file-sharing story end to end, not just that the API
accepted a request.

The target hardware is an Intel NUC (x86_64). All development and testing
happened on Apple Silicon (aarch64) VMs, since that's what was available.
**The very first time this code ran on the actual target architecture was
the user's own deployment** — there is no architecture-specific code, and a
later real-hardware report (see "Bugs found after real deployment" below)
confirmed the build works unmodified on x86_64, but this gap is worth
knowing about if something arch-specific ever turns up.

## Design decisions worth knowing, and why

**Nothing is shared by default.** The share registry (`/data/shares.json`)
starts empty. A share exists only because someone explicitly right-clicked a
folder and confirmed. This was a deliberate security choice, not an
oversight — a NAS holds everything, and SMB on a home LAN is reachable by
every device on that LAN, so default-share-everything has an unacceptable
blast radius for one mistake.

**`smb.conf` is never rewritten by the app.** It owns exactly one generated
include file (`/etc/samba/shares.d/dashboard-shares.conf`), regenerated in
full from the JSON registry on every change, validated with `testparm`
before being applied, and reloaded (not restarted) via `smbcontrol` so
existing SMB sessions on other shares survive a share being added or
removed elsewhere.

**Host stats, not cgroup stats — but the received wisdom about *why* is
wrong.** The app binds `psutil.PROCFS_PATH` to a read-only mount of the
host's `/proc`. Measured under a forced `--memory=512m` cgroup cap: on
*plain* Docker, `/proc/meminfo` and `/proc/stat` are **already** the host's —
they are not namespaced — so the bind mount is defensive, not decisive,
there. What actually matters is `/sys` (not `/proc`): without it, the
network widget isn't degraded, it's *empty*, because the container's own
`eth0` resolves under `devices/virtual/net` and gets correctly-but-uselessly
classified as virtual, and CPU temperature/GPU disappear entirely. The
`/proc` bind earns its keep specifically under **lxcfs** (standard on LXD,
common on Proxmox), which *does* virtualize `/proc/meminfo` to the cgroup
limit — that's the actual scenario it protects against, not plain Docker.
See the README's "Host stats, not cgroup stats" section for the full
measured comparison table.

**Every filesystem path is resolved and containment-checked against its
volume root** (`backend/app/services/fs.py`) before any operation touches
disk. Verified against traversal (`../../etc/passwd`) and — the case that
actually matters — a **symlink planted inside a shared folder pointing at
`/etc`**, which is refused because containment is checked against the
*resolved* real path, not the literal string.

**SMB user Unix accounts use a fixed uid range, not system-allocated ones.**
The container's `/etc/passwd` is not part of any volume and is discarded on
every rebuild, while Samba's password database (`/var/lib/samba`) and the
app's own registry (`/data`) both persist. Without fixed, remembered uids,
every SMB login would start failing after `docker compose up --build` with
no obvious cause — this was found by testing, not designed in advance (see
below).

**Apps launcher has no add/edit UI, on purpose.** It's a Homer-style linked-
services page, configured by hand-editing `./data/apps.yml` directly on the
host. This is deliberately *not* symmetric with how the rest of the app
treats configuration (as UI state) — the list is exactly the kind of thing
set up once and barely touched, and a flat file is easier to back up or
script than a form needs to be.

## Real bugs found by testing — and the pattern behind them

Every one of these was found by *running the thing*, not by reading the
code. Full detail and reproduction steps are in `TEST_RESULTS.md`; the short
version, because the pattern itself is worth internalizing:

1. **Container couldn't start at all** on any machine without `/dev/sda` —
   the shipped compose file's `devices:` block had a guessed device name.
   Docker refuses to *start* a container whose `devices:` names a
   non-existent node. Fixed by shipping `devices:` commented out with
   instructions, rather than guessing.
2. **`smbd` crash-looped forever** on first run — `secrets.tdb` couldn't be
   created because mounting a fresh host directory over `/var/lib/samba`
   hides the `private/` subdirectory the Debian package expects to already
   exist. Fixed by having the entrypoint recreate that skeleton on every
   boot.
3. **Every SMB connection failed**, `IPC$` included, with
   `NT_STATUS_BAD_NETWORK_NAME` — `fruit`/`streams_xattr` (macOS
   interop) aren't part of Debian's `samba` package, they're in
   `samba-vfs-modules`, and a *global* `vfs objects` line in `smb.conf`
   applies to `IPC$` too, so one missing module took down every
   connection instead of degrading one share.
4. **SMB users silently broke on every rebuild** — see the fixed-uid design
   decision above. This one is the most dangerous in the list: it produces
   `NT_STATUS_LOGON_FAILURE` with a *correct* password and no obvious cause,
   which is a nasty thing to debug at 2am.
5. **A context-menu bug found on the user's real, deployed NUC** (not caught
   by any of this repo's own testing beforehand): right-clicking a folder
   opened the menu, but no menu item did anything — no request ever reached
   the backend. Root cause: the outside-click dismiss listener ran in the
   *capture* phase on `document`, which fires before the event reaches (or
   bubbles back out of) the menu's own buttons, so it closed the menu on
   every click inside it too, before a button's own `onClick` ever got a
   turn. `stopPropagation()` on the menu container couldn't prevent this —
   that's a bubble-phase handler, and capture-phase listeners on an ancestor
   always run first regardless of what a descendant does later. Fixed by
   checking click containment (`ref.current.contains(e.target)`) in the
   capture handler itself. Reproduced and re-verified with a **real
   Puppeteer mouse-click sequence** (`page.mouse.click`, not
   `element.click()`, which bypasses the exact event ordering the bug lived
   in) before and after the fix.

The throughline: **this app had a working automated test suite the whole
time (pytest, ~50+ tests), and none of these bugs were caught by it**,
because they all live in the gap between "the code is logically correct"
and "the code behaves correctly under the real event/container/filesystem
model it actually runs in." Unit tests verify logic; they don't verify
Docker's `devices:` semantics, Samba's package layout, browser event
dispatch order, or container filesystem lifecycle. When in doubt, run the
real thing.

## A CI/automation incident worth knowing about

Dependabot + an auto-merge GitHub Actions workflow were set up, but branch
protection requiring the CI checks to pass was configured *after* auto-merge
was already enabled — a sequencing mistake. Result: several dependency
bumps (including a broken `react`/`react-dom` version mismatch) merged to
`main` completely unvetted, because auto-merge had nothing to wait for.
Caught, all merges reverted via `git revert` (not a history rewrite), and
replaced with the current setup: **no continuous Dependabot** — instead, the
extracted [dependency-update-workflow](https://github.com/codemastervy/dependency-update-workflow)
runs every 2 months (or on demand), checks for updates, and **pauses on a
GitHub Environment requiring manual approval** before applying anything.
Only after approval does it open a PR, and that PR still has to pass the
same required CI checks (`backend-tests`, `frontend-build`, `docker-build`)
as any other change — this was verified by pushing a deliberately broken
dependency and confirming GitHub itself refused to merge it.

**Practical implication:** branch protection on `main` requires those three
checks and applies to direct pushes too (`enforce_admins: true`). A plain
`git push` to `main` will be rejected unless the commit already has passing
check-runs recorded against its exact SHA — go through a PR.

## Current known gaps (not bugs — genuinely unverified)

Stated honestly rather than glossed over, from `TEST_RESULTS.md`:

- **SMART health**: the code path (device enumeration, capability
  detection, graceful degradation, on-demand scan) is exercised and
  unit-tested against captured real `smartctl -j` output, but never against
  a physical SATA/NVMe drive — the VM this was built in had none. On a real
  NUC, SMART needs `devices:` and `cap_add: [SYS_RAWIO, SYS_ADMIN]`
  uncommented in `docker-compose.yml` and pointed at the real disk device;
  it degrades to "unavailable" with a specific reason otherwise, never
  fabricates a health status.
- **The dashboard has no way to see SMART scans run outside its own
  "Scan now" button** — if you already have your own `smartd`/cron routine,
  the dashboard won't reflect it. This surfaced as a real user report after
  deployment and turned out not to be a bug — it's a genuine missing
  capability (auto-periodic scanning was proposed and explicitly declined).
- **GPU detection**: implemented for NVIDIA (`nvidia-smi`), AMD, and Intel
  (sysfs), but never exercised against real hardware of any kind.
- **WiFi vs. Ethernet classification**: the logic (keyed on `phy80211`
  presence in sysfs, not interface names) is unit-tested against a
  synthetic sysfs tree, and Ethernet classification is verified live —
  but no VM used here has ever had a real wireless radio to test against.
- **Physical phone browser**: the responsive layout was verified in a real
  Chromium engine at a phone viewport/DPI (via Puppeteer), which is
  stronger than devtools emulation, but genuine touch behavior — the
  long-press-to-open-context-menu gesture, iOS Safari's viewport quirks —
  has never been checked on an actual handset.
- **CasaOS migration steps** in the README are derived from what CasaOS is
  known to install (host `smbd`/`nmbd`, a web UI on :80), written
  conservatively (back up before touching anything, verify a second device
  can connect before removing CasaOS) — but never walked through against a
  real CasaOS installation.

## If you're picking this up fresh

- Read the README's numbered sections in order; it's organized as
  problem → decision → tradeoff, not just a feature list.
- Read `TEST_RESULTS.md` for the exact commands and outputs behind every
  verification claim above.
- Branch protection means you cannot push straight to `main` — branch,
  open a PR, wait for the three required checks, merge.
- If you're testing something that needs Docker and don't have a spare
  Linux box: a Lima VM (`vmType: vz`, Ubuntu 24.04 cloud image, Docker
  installed via the official apt repo) reproduces the target environment
  closely enough that every bug in the list above was found this way.
