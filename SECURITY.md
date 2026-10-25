# Security Policy

## Reporting a Vulnerability

If you believe you have found a security vulnerability in SwarmLLM-Bench, please
**do not open a public issue**. Instead, report it privately via GitHub's
[private vulnerability reporting](https://github.com/<owner>/SwarmLLM-Bench/security/advisories/new)
feature so it can be triaged and fixed before public disclosure.

Please include:

- A description of the vulnerability and its impact.
- Steps to reproduce it.
- The affected component / file / dependency version.

We aim to acknowledge reports within 3 business days and publish a fix (with a
GitHub Security Advisory) as soon as one is available.

## Supported Versions

Only the latest `main` branch is currently supported with security updates.

| Version / Branch | Supported          |
| ---------------- | ------------------ |
| `main`           | :white_check_mark: |
| older tags       | :x:                |

## Automated Security Scanning

This repository runs automated checks on every pull request and push to `main`
(see `.github/workflows/security.yml`):

- **CodeQL (SAST)** — static analysis of Python source for common weakness
  classes (injection, path traversal, unsafe deserialization, etc.).
- **pip-audit (SCA)** — scans `requirements.txt` against OSV/PyPA/GHSA
  databases for known CVEs in dependencies.
- **Dependabot** — opens automated PRs to patch vulnerable or outdated
  dependencies (see `.github/dependabot.yml`).

In addition, enable **secret scanning** in the repository settings
(*Settings → Code security and analysis → Secret scanning*) to detect
hardcoded credentials in commits. Note that `config.py` currently stores the
LLM API key directly in source (`LLM_API_KEY`); move it to an environment
variable or a git-ignored `.env` file before using a real key.
