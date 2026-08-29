# Test results

Everything below was run against a real Ubuntu 24.04 host with a real Docker
daemon, a real Samba server, and a real SMB client on a second machine. Where
something could not be verified, it says so and why — nothing here is inferred
from "the config looks right".

---

## Test environment, and its one honest caveat

| | |
|---|---|
| Host OS | Ubuntu 24.04.4 LTS, kernel 6.8.0-138-generic, aarch64 |
| Docker | 29.7.2, Compose v5.5.0, cgroup **v2** |
| Samba (in container) | 4.17.12-Debian |
| Host resources | 4 vCPU, 4 GiB RAM |
| Storage under test | two separate ext4 filesystems on distinct block devices (`/dev/vdb1`, `/dev/vdc1`) |
| Second device (SMB client) | macOS 26.5.2, `mount_smbfs` / `smbutil` — Apple's own SMB stack |

**The caveat.** The target machine is a physical NUC. That machine was not
available to test against, so the Ubuntu host used here is a virtual machine
(Lima + Apple Virtualization) running on the Mac. Ubuntu, Docker, the kernel,
Samba, ext4 and the SMB protocol are all genuine; the *hardware* is not. The
consequences are stated precisely in [Not verified](#not-verified) — they
affect SMART, GPU and WiFi, and nothing else.

The macOS machine is a genuinely separate host from the server: separate
kernel, separate SMB implementation, reaching the server over TCP/445.

---

## Summary

| Area | Result |
|---|---|
| Container build (multi-stage, Vite + FastAPI + Samba) | **Pass** |
| Container runs, all three processes stable | **Pass** |
| Host stats vs container cgroup | **Pass** — see below, with a correction to received wisdom |
| File browser: all operations | **Pass** |
| Path traversal / symlink escape | **Pass** — all refused |
| SMB share creation from the API | **Pass** |
| SMB connect from a second device | **Pass** — macOS Finder stack |
| Per-user read-only vs read-write | **Pass** — enforced, not cosmetic |
| Non-member denied | **Pass** |
| Unshare actually revokes access | **Pass** — including severing a live mount |
| Users survive a container rebuild | **Pass** *(after fixing a real bug — see below)* |
| Dashboard auth blocks unauthenticated access | **Pass** |
| Automated test suite | **Pass** — 40 tests |
| SMART on real hardware | **Not verified** — no SMART-capable drive available |
| GPU | **Not verified** — no GPU in the VM |
| WiFi vs Ethernet on real hardware | **Partly** — logic tested against synthetic sysfs, no real radio |

---

## Bugs found by testing, and fixed

These were all found by running the thing, not by reading it. Each would have
hit you on first use.

### 1. Container refused to start on any machine without `/dev/sda`

The shipped `docker-compose.yml` listed `- /dev/sda:/dev/sda` under `devices:`.
Docker refuses to **start** a container whose `devices:` names a node that does
not exist:

```
Error response from daemon: error gathering device information while adding
custom device "/dev/sda": no such file or directory
```

On an NVMe-only NUC, first boot would simply fail. **Fixed:** all `devices:`
entries are commented out by default, with instructions. SMART is optional; a
container that starts is not.

### 2. `smbd` crash-looped on first run — `secrets.tdb`

Mounting a fresh host directory at `/var/lib/samba` (needed so SMB users
survive a rebuild) hides the `private/` subdirectory the Debian package
creates. Samba does not rebuild it:

```
Failed to open /var/lib/samba/private/secrets.tdb
exit_daemon: daemon failed to start: smbd can not open secrets.tdb, error code 13
```

smbd restarted every ~2 seconds, forever. **Fixed:** the entrypoint recreates
`/var/lib/samba/private` (mode 0700) and `usershares` on every start.

### 3. Every SMB connection failed — missing VFS modules

With smbd finally running, **every** tree connect failed, `IPC$` included:

```
tree connect failed: NT_STATUS_BAD_NETWORK_NAME
```

The real cause was in Samba's log, not the client's:

```
Error loading module '/usr/lib/aarch64-linux-gnu/samba/vfs/streams_xattr.so':
  cannot open shared object file: No such file or directory
make_connection_snum: vfs_init failed for service IPC$
```

`fruit` and `streams_xattr` (macOS interoperability) are **not** part of
Debian's `samba` package — they live in `samba-vfs-modules`. **Two fixes:**
install that package, *and* stop declaring `vfs objects` in `[global]`, because
a global module list is applied to `IPC$` too, so one missing module takes down
every connection instead of degrading one share.

### 4. Every SMB user broke after `docker compose up --build`

The most damaging one. Samba's password database persists (it is on a volume),
and the app's user registry persists — but the **Unix accounts** those map to
live in the container's `/etc/passwd`, which is thrown away on every rebuild.
Result: correct password, `NT_STATUS_LOGON_FAILURE`, no obvious cause.

**Fixed** with two changes: uids and the shared gid are assigned from a fixed
range and recorded in the registry, and a `reconcile()` pass at startup
recreates any missing Unix account with its original uid. Verified across a
rebuild:

```
INFO app.services.smbusers: restored 2 Unix account(s) for SMB users: sam, alex
sam:x:3000:3000::/home/sam:/usr/sbin/nologin
alex:x:3001:3001::/home/alex:/usr/sbin/nologin
nasusers:x:2999:sam,alex
```

Stable ids also matter for file ownership: without them, files already written
to your disks would end up owned by whoever happened to get uid 1000 next time.

### 5. Hostname displayed as a container id

The dashboard showed `4935dedacacb` instead of the machine name.
`/proc/sys/kernel/hostname` is **UTS-namespaced**, so reading it through the
`/host/proc` bind mount still returns the container's own hostname — a bind
mount cannot see through a namespace. It happened to look right only because
`network_mode: host` makes Docker inherit the host's name.

**Fixed:** prefer an explicit `HOST_HOSTNAME`, then a `/etc/hostname` bind
mount, then the namespaced value. Verified in both modes:

```
host-network stack : lima-nas via /host/etc/hostname   (host: lima-nas)
bridged container  : lima-nas via /host/etc/hostname   (was: 4935dedacacb)
```

---

## Host statistics, not cgroup statistics

The dashboard's figures were compared field by field against the host:

| metric | host | dashboard | |
|---|---|---|---|
| memory total (bytes) | 4093878272 | 4093878272 | ✅ |
| logical CPUs | 4 | 4 | ✅ |
| hostname | lima-nas | lima-nas | ✅ |
| uptime (s) | 1031 | 1031 | ✅ |
| kernel | 6.8.0-138-generic | 6.8.0-138-generic | ✅ |

`GET /api/health` reports `"host_proc_bound": true`.

### Forcing the failure condition

A comparison on an unconstrained host proves little, so the container was re-run
under a hard cgroup cap — `--memory=512m` on a 4 GB host — and then with the
host mounts removed entirely:

| | both binds | **no binds** |
|---|---|---|
| cgroup `memory.max` inside container | 536870912 | 536870912 |
| `memory.total` reported | 4093878272 ✅ | 4093878272 ✅ |
| `cpu.cores_logical` | 4 ✅ | 4 ✅ |
| CPU temperature | host hwmon | **`null`** ❌ |
| `eth0` classified as | **`ethernet`** ✅ | **`virtual`** ❌ |
| physical NICs shown | 1 | **0 — widget empty** ❌ |
| GPU detection | possible | impossible ❌ |

### A correction to the usual advice

This is worth stating plainly because it contradicts what the project set out
to assume, including an earlier draft of this repo's own README:

- **`/sys:/host/sys:ro` is the load-bearing mount.** Without it the network
  widget is not degraded, it is *empty* — the container's own `eth0` correctly
  resolves under `devices/virtual/net` and is classified as virtual. CPU
  temperature and GPU vanish too.
- **`/proc:/host/proc:ro` is defensive, not decisive on plain Docker.**
  `/proc/meminfo` and `/proc/stat` are not namespaced, so memory and CPU come
  out right with or without it (confirmed above, under a 512 MB cap). It earns
  its place where something *virtualises* procfs — **lxcfs**, standard on LXD
  and common on Proxmox, rewrites `/proc/meminfo` to the cgroup limit. There,
  the explicit bind is the difference between right and wrong.

The mount is kept, and the reasoning is now documented accurately rather than
repeated as folklore.

---

## SMB: the full lifecycle, verified against a second machine

### Share creation

Created via the API with per-user access — `sam` read-write, `alex` read-only.
Generated fragment (`data/shares.d/dashboard-shares.conf`):

```ini
[Documents]
   comment = Family documents
   path = /mnt/storage/data/Documents
   browseable = yes
   guest ok = no
   valid users = alex sam
   read only = yes
   write list = sam
   ...
```

Samba reloaded via `smbcontrol all reload-config` — a reload, not a restart, so
existing sessions on other shares are not dropped. Registry and Samba agree:

```json
{"running": true, "registry_shares": ["Documents"], "active_shares": ["Documents"]}
```

### Connection from macOS — a genuinely separate machine

```
$ smbutil view -N //sam:...@127.0.0.1
Share            Type    Comments
IPC$             Pipe    IPC Service (lima-nas (nas-dashboard))
Documents        Disk    Family documents
2 shares listed

$ mount_smbfs //sam:...@127.0.0.1/Documents ./mnt-sam
//sam@127.0.0.1/Documents on .../mnt-sam (smbfs, nodev, nosuid)
```

Read and wrote from the Mac; the file appeared immediately in the dashboard's
file browser (round trip confirmed both ways, see `docs/screenshots/files.png`).

### Permissions are real, not cosmetic

| Test | Expected | Actual |
|---|---|---|
| `sam` (rw) writes a file | succeeds | ✅ uploaded |
| `alex` (ro) reads | succeeds | ✅ listing returned |
| `alex` (ro) writes | denied | ✅ `NT_STATUS_ACCESS_DENIED` |
| `alex` (ro) deletes another user's file | denied | ✅ `NT_STATUS_ACCESS_DENIED` |
| `sam` with wrong password | denied | ✅ `NT_STATUS_LOGON_FAILURE` |
| `guestuser` (not a member) connects | denied | ✅ `NT_STATUS_ACCESS_DENIED` |

### Unsharing genuinely revokes access

This was the specific thing worth being suspicious about — that "Unshare" might
only hide a row in the UI. It does not:

| Check | Result |
|---|---|
| Share section removed from generated config | ✅ file back to "No shares" |
| Share gone from the client's share list | ✅ only `IPC$` remains |
| New connection attempt by `sam` | ✅ `NT_STATUS_BAD_NETWORK_NAME` |
| **Already-mounted macOS session** | ✅ **severed** — the mountpoint became invalid |
| Re-mount attempt from macOS | ✅ refused |
| Files still on disk | ✅ intact, untouched |

The live-session teardown is `smbcontrol smbd close-share`. Unsharing revokes
access *now*, not at the next reconnect.

---

## Dashboard authentication

| Request | Expected | Actual |
|---|---|---|
| `GET /api/system/stats` with no cookie | 401 | ✅ 401 |
| `GET /api/files/volumes` with no cookie | 401 | ✅ 401 |
| `GET /api/shares` with no cookie | 401 | ✅ 401 |
| `POST /api/auth/login` wrong password | 401 | ✅ 401 |
| `POST /api/auth/login` correct password | 200 + cookie | ✅ 200 |
| `GET /api/system/stats` with cookie | 200 | ✅ 200 |

With `ADMIN_PASSWORD` unset the API returns **503** on every data route rather
than serving openly — it fails closed.

---

## Path safety

Every attempt to escape a mounted volume was refused:

| Attempt | Result |
|---|---|
| `/data/../../etc/passwd` | 400 — `path may not contain '..'` |
| `/data/..%2F..%2Fetc` | 404 |
| `../etc/passwd` | 400 |
| `/etc/passwd` | 404 — `no such volume: etc` |
| `/data/./../../root` | 400 |
| symlink `/data/escape-link` → `/etc` | **403 — `path escapes its volume`** |
| listing *through* it, `/data/escape-link/passwd` | **403** |
| symlink to a file, `/data/passwd-link` → `/etc/passwd` | **403** |
| **downloading** through that symlink | **403** |
| deleting a volume root `/data` | refused |

The symlink cases matter most: the containment check compares *resolved* paths,
so a symlink planted inside a shared folder cannot be used to read the host.

---

## File operations

| Operation | Result |
|---|---|
| `mkdir` | ✅ |
| Upload (200 KB, streamed) | ✅ |
| Upload same name again | ✅ saved as `upload-me 2.bin` — de-duplicated, not overwritten |
| Rename | ✅ |
| Copy **across volumes** (`/media` → `/data`) | ✅ |
| Move | ✅ |
| Download, byte-for-byte | ✅ **md5 match** with the original |
| Recursive search | ✅ |
| Batch delete (folder + file) | ✅ |
| Delete a volume root | ✅ **refused** |

---

## Automated tests

40 tests, run inside the container (`python -m pytest tests -q` → `40 passed`).
They cover what live hardware could not:

- **`test_smart_parsing.py`** — SMART parsing against captured `smartctl -j`
  output for SATA and NVMe: healthy pass, outright fail, and the important
  case of a drive reporting **PASS while reallocating sectors** (surfaced as
  `warning`). Also that a missing `smart_status` yields `unknown`, never a
  fabricated `pass`, and that the whole-disk regex accepts `sda`/`nvme0n1` but
  rejects partitions like `sda1`/`nvme0n1p2`.
- **`test_interface_classification.py`** — WiFi/Ethernet/virtual against a
  synthetic `/sys/class/net`, deliberately using misleading names: `enp3s0`
  with a `phy80211` link is **wifi**, while `wlan9` without one is **not**.
  Confirms `docker0`, `veth*` and bridges are excluded.
- **`test_path_safety.py`** — traversal, symlink escape, volume-root
  protection, collision de-duplication.
- **`test_share_config.py`** — generated `smb.conf`, including that a share
  with no members does not become world-readable, and that a newline in a
  comment cannot inject an `admin users` directive.

---

## Not verified

Stated plainly rather than glossed.

### SMART on real drives — **not verified**

The code path runs correctly end to end: it enumerates block devices, invokes
`smartctl`, parses the result, and reports failure honestly rather than
inventing health:

```json
{"device": "/dev/vda", "status": "unknown",
 "error": "/dev/vda: Unable to detect device type"}
```

That is smartctl's genuine response — **virtio disks do not implement SMART**,
so no VM can verify this. What is verified: device enumeration, capability
detection, the privilege check, graceful degradation, the on-demand scan
endpoint, and the parsing logic (against captured real-drive output, above).

What is **not** verified: reading SMART from physical SATA/NVMe hardware
through `cap_add: [SYS_RAWIO, SYS_ADMIN]`. On your NUC, uncomment `devices:`
in the compose file and press **Scan now**. If the capabilities are wrong the
UI will tell you exactly which one is missing.

### GPU — **not verified**

No GPU is exposed to the VM. The dashboard correctly reports
`"no readable GPU found"` rather than erroring, which is the required
no-GPU behaviour. Intel iGPU detection on your NUC (via
`/sys/class/drm/card*/device`, vendor `0x8086`) is unexercised. Note that the
Intel utilisation figure is a **frequency ratio, not true busy time**, and the
UI labels it as an estimate — `intel_gpu_top` needs `CAP_PERFMON` and is too
slow to poll.

### WiFi vs Ethernet on real hardware — **partly verified**

The VM has one virtio NIC and no radio. Ethernet classification is verified
live (`eth0` → `ethernet`, `docker0`/`veth*` → `virtual`). WiFi detection is
verified only against a synthetic sysfs tree that reproduces the kernel's
layout. The logic keys on `phy80211`/`wireless` in sysfs rather than interface
names, which is the part most likely to be got wrong; on your NUC both
interfaces should appear with correct labels.

### Physical-hardware temperatures — **not verified**

No `hwmon` sensors exist in the VM, so `cpu.temperature_c` is `null` and the UI
omits the badge. Reading real `coretemp`/`k10temp` on the NUC is unexercised;
the fallback chain (hwmon → thermal zones) is implemented but untested against
hardware.

### Phone browser — **not verified on a physical phone**

The responsive layout was rendered at a 390×844 viewport at 2× DPI in a real
browser engine (Chromium via Puppeteer), not in devtools' device emulation —
the sidebar collapses to a hamburger, cards stack, and the file browser drops
its size/date columns (`docs/screenshots/mobile.png`). What that cannot verify
is genuine touch behaviour: the long-press-to-open-context-menu gesture, and
iOS Safari's viewport quirks. Both need a real handset on your LAN.

### CasaOS migration — **not verified**

No CasaOS installation was available. The migration steps in the README are
derived from what CasaOS installs (host `smbd`/`nmbd`, its web UI on :80, its
own Docker containers) and are written to be conservative — back up
`/etc/samba` **and** `/var/lib/samba`, stop but do not uninstall the host
Samba, and remove CasaOS only after a second device has connected successfully.
Please follow them in that order rather than trusting them blind.

---

## Reproducing this

```bash
cp .env.example .env && $EDITOR .env      # set ADMIN_PASSWORD
$EDITOR docker-compose.yml                # point volumes at your disks
docker compose up -d --build

curl -s localhost:8080/api/health         # host_proc_bound must be true
docker compose exec nas-dashboard python -m pytest tests -q
```

Then create a user, share a folder, and connect from another machine —
because that is the only test that actually counts.

---

## Re-verification: fresh clone, clean build, full chain

A second, independent test pass, run against a fresh `git clone` of this
repo's `main` branch on a newly created Ubuntu 24.04 + Docker VM — not the
same container instances used above, so this confirms the repo works for
someone starting from nothing, not just that a running instance kept working.

**Result: pass.**

- `docker compose build` succeeded on the first try, no manual fixes needed —
  confirms the VFS-module and `secrets.tdb` fixes are actually in the pushed
  code, not just in a container that happened to already be patched.
- Container came up `healthy` on first boot.
- Created two SMB users and a share through the API, exactly as a real user
  would through the UI.
- **Connected from macOS's own SMB client** (`mount_smbfs`) as a genuinely
  separate device: mounted the share, read the existing file, wrote a new one.
- Read-only enforcement re-confirmed: a write attempt from a read-only user
  returned `NT_STATUS_ACCESS_DENIED` from Samba itself.
- Wrong password re-confirmed: `NT_STATUS_LOGON_FAILURE`.
- Unshare re-confirmed to revoke access immediately: the next connection
  attempt got `NT_STATUS_BAD_NETWORK_NAME`.
- Host stats re-confirmed accurate: `hostname` reported by the API
  (`lima-nas`) matched the VM's own `hostname` command output exactly, and
  `host_proc_bound` was `true`.
- Full automated suite: **40/40 passed**, from a fresh install of the tests
  into the built image (not carried over from a prior run).

Same caveat as the rest of this document: this VM is aarch64, not the target
NUC's presumed x86_64. Nothing else changed between runs.
