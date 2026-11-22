# Raspberry Pi 5 pilot

Use 64-bit Raspberry Pi OS with Python >=3.11. Unpack this release into a new writable directory. Confirm the ZIP digest against the release digest distributed alongside it. Run the inspection and disposable qualification as your ordinary account:

```sh
bash deploy/pi5/inspect.sh
bash deploy/pi5/verify.sh "$HOME/muse030-pilot-results"
```

The output path must be new and outside the extracted release. Record architecture, OS, Python and SQLite versions, free storage, and Pi temperature and throttle flags. These commands don't install packages, create a service, or activate a release. A successful summary is the pilot acceptance result for the target hardware.

## Local service trial after the pilot

Place the verified source in a root-owned, world-readable release directory under /opt/muse030/releases and point /opt/muse030/current at it. Do not give the service user ownership of program files. The example unit requires an unprivileged muse user and group. Provision /var/lib/muse030 as muse:muse mode 0700. From the verified source directory, initialize /var/lib/muse030/state as muse with a private umask and send the emitted token JSON to a private operator file. Use the systemd unit on a disposable service state directory first. Confirm loopback binding, service restart, and authenticated calls before using persistent state.

## State protection and rollback

Stop and confirm all writers have exited before a snapshot. Back up the entire private state directory together, including credentials, the observation database, the Suit sidecar if enabled and any associated SQLite files. Keep the stopped-service snapshot private and record the release digest and schema context. Restore a copy into a separate private directory and verify the expected records and trace before activation.

Keep the previous verified release available. For rollback, stop the service and all other writers, restore the state snapshot that matches the preceding release if schemas changed, update the symlink atomically, and verify authenticated reads before accepting writes. The pilot qualification and process-exit injection leave real device reboot and coordinated restore as operator acceptance checks.
