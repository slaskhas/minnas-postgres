# Security Policy

## Reporting a vulnerability

Please do not report publicly. Create a [Security Advisory](https://github.com/gymaira1990-jpg/Mnemosyne-OS/security/advisories) on GitHub to report privately, or email the contact address shown on the repo homepage.

## Supported versions

| Version | Supported |
|------|------|
| v7.x | ✅ Current |
| v6.x | ⚠️ Security fixes only |
| < v6 | ❌ |

## Security measures

- GitHub Secret Scanning + Push Protection enabled
- Every push is automatically scanned for leaked secrets
- `.gitignore` excludes `.env`, `*.pem`, `*.key`
- The repo contains schema only (zero data) — no real memory content of any kind
