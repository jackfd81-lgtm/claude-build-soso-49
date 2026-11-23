# Raspberry Pi 5 Deployment & Recovery Guide (MUSE Build 030.1 CRAM-PU)

## 1. Python & Dependencies
- **Python:** 3.11+ (stdlib only for CRAM-PU, `Big49`/`Small49`, and `qualification_030.verify`; no external PyPI dependencies required at runtime).
- **Installation:**
  ```bash
  sudo mkdir -p /opt/muse030_1 /var/lib/muse030_1/{warm,cool,brains} /var/log/muse030_1
  python3 -m venv /opt/muse030_1/.venv
  /opt/muse030_1/.venv/bin/pip install --no-index --find-links=/opt/muse030_1/dist muse_build030
  ```

## 2. Storage & Durability Assumptions
- **Do not assume `/tmp` is `tmpfs`:** On Raspberry Pi OS, `/tmp` may reside on the root SD/NVMe partition. Point `WARM_DIR` explicitly to a dedicated directory (`/var/lib/muse030_1/warm` or a separately mounted `tmpfs` with `size=256M,mode=0700` if volatile RAM staging is desired).
- **WARM vs. COOL:** `WARM_DIR` holds disposable candidate snapshots; `COOL_DIR` (`registry.sqlite3` with `WAL` + `synchronous=FULL`) and `BRAINS_DIR` (`big49.sqlite3`, `small49.sqlite3`) are the durable stores.
- **Power-Loss Reality:** POSIX `fsync()` alone does not guarantee survival against every physical power failure if disk/SD controller write-caching is enabled. Use an ext4/f2fs filesystem with barriers enabled on reliable NVMe/industrial SD media (and UPS/power-fail protection for field deployments), and verify with `cram-pu-setup.sh` + `qualification_030.verify`.

## 3. Monitoring, Resource Limits, & Reboot Recovery
- `muse030-cram.service` caps `MemoryMax=1536M` and `CPUQuota=200%` and runs `cram-pu-setup.sh` in `ExecStartPre` to verify `>= 512 MB` free space and write+fsync capability.
- On service restart after crash, call `Merged49BrainsCramAdapter.sync_pending_outbox()` and `recover_missing_mirrors()` to drain any `PENDING` or `APPLIED` outbox entries into `Big49`/`Small49`.

## 4. Safe Rollback
1. Stop service: `sudo systemctl stop muse030-cram.service`
2. Snapshot `COOL_DIR` and `BRAINS_DIR` (`sqlite3 ... ".backup ..."`).
3. Re-install previous `30.0.1` wheel (`dist/muse_build030-30.0.1-py3-none-any.whl`) and restart.
