# Security & responsible use

## Scope

This project provides **local** control of Haier air conditioners you own, on your own network. At
runtime it talks only to your ACs over the LAN (`:56800`). Your Haismart account is used at setup (and
on key rotation) to fetch each device's per-device `localKey` — the same operation the official app
performs.

## Handling secrets

- Your account credentials, `refreshToken`, and each AC's `localKey` are **secrets**. Keep them out of
  logs, screenshots, and public issues.
- Do **not** commit real credentials. `*.local.json` and similar are git-ignored; scrub any pasted logs.
- The **Local key** diagnostic sensor is disabled by default because its value is a secret.

## Reporting a vulnerability

Please report security issues privately via a GitHub **Security Advisory** on this repository (or a
direct message to the maintainers) rather than a public issue. Include steps to reproduce and affected
versions. We aim to acknowledge within a few days.

## Responsible use

Use this only with hardware you own or are authorized to control. It is an interoperability tool, not a
means to access devices or accounts that aren't yours.
