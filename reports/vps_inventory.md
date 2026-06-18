# VPS Inventory Report
Generated: 2026-06-18

## Hardware

| Item | Value |
|------|-------|
| Provider | Google Cloud Platform (GCP) asia-southeast1-b |
| CPU | AMD EPYC 7B12 × 2 vCPUs |
| RAM | 3.8 GiB total / 2.4 GiB used / 1.5 GiB available |
| Swap | 2.0 GiB / 718 MiB used |
| Disk | 38 GiB total / 35 GiB used / **3.4 GiB free (92% used) ⚠️ CRITICAL** |

## OS

| Item | Value |
|------|-------|
| OS | Ubuntu 24.04.4 LTS (Noble Numbat) |
| Kernel | Linux 6.17.0-1018-gcp (GCP optimised) |
| Architecture | x86-64 |
| Hostname | auto-trade-vps.asia-southeast1-b.c.auto-489108.internal |

## Network

| Item | Value |
|------|-------|
| Public IP | 35.247.178.143 |
| Private IP | 10.148.0.2 (GCP internal) |
| Tailscale IP | 100.106.165.119 / fd7a:115c:a1e0::9636:a577 |
| Nearest DERP | Singapore (1.6ms) |

## Firewall

| Item | Status |
|------|--------|
| UFW | INACTIVE (GCP VPC firewall handles perimeter) |
| fail2ban | RUNNING (SSH protection active) |
| GCP VPC | Managed externally — not inspectable from inside VPS |

## Tailscale Status

| Item | Status |
|------|--------|
| tailscaled.service | running |
| Authenticated as | aung.pro1@ |
| Tailscale IP | 100.106.165.119 |
| **Exit node advertised** | **YES — "offers exit node"** |
| IPv4 forwarding | ENABLED (net.ipv4.ip_forward = 1) |
| IPv6 forwarding | ENABLED (net.ipv6.conf.all.forwarding = 1) |
| Direct UDP | YES (35.247.178.143:36717) |
| IPv6 direct | No |

## Running Services (relevant)

| Service | Status | Notes |
|---------|--------|-------|
| smc-bot.service | **active (running)** — 16h uptime | Trading bot, SMC signal loop |
| tailscaled.service | active (running) | Tailscale daemon |
| docker.service | running | No active containers |
| fail2ban.service | running | SSH brute-force protection |
| ssh.service | running | Port 22 open |
| unattended-upgrades.service | running | Auto security patches |

## Open Ports (external-facing)

| Port | Protocol | Service |
|------|----------|---------|
| 22 | TCP | SSH |
| 8000 | TCP | ag-auto-trade dashboard (uvicorn, 0.0.0.0 — public!) |
| 41641 | UDP | Tailscale WireGuard/STUN |

⚠️ **Port 8000 is publicly exposed** on 0.0.0.0 — ag-auto-trade dashboard has no GCP VPC firewall rule shown but should be verified.

## Trading Systems Detected

| System | Path | Status |
|--------|------|--------|
| SMC Bot (System 1) | ~/simple-smc-ag-trading-bot/ | `smc-bot.service` active |
| AG Auto-Trade Dashboard | ~/ag-auto-trade/ | uvicorn on :8000, PID 709361 |
| Pionex System (System 2) | ~/pionex-trade-system/ | Not checked this phase |
