# Orange Pi Zero 2W Independent Tester & Grader

The Orange Pi Zero 2W acts as an **independent external tester and grader** for the Raspberry Pi 5 CRAM-PU + Build 030.1 runtime.

## Security Boundary
- The Orange Pi **never** receives the Pi 5 `admin_token`, direct SQLite file write access, or SoSo Suit capability-granting authority.
- It submits only authorized agent workloads and grades responses, latency, CPU/RSS contention, isolation, revision consistency, and restart recovery.

## Local Simulation Run
```bash
python3 deploy/orangepi/orangepi_grader.py --iterations 20
```

## Unverified Hardware Scope
Physical Ethernet/UART transport between Orange Pi Zero 2W and Raspberry Pi 5 and real hardware thermal/SD I/O contention remain unverified until executed on the target boards.
