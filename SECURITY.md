# Security Policy

Resona handles user credentials, session cookies, and e-mail addresses, so
responsible disclosure is welcome and appreciated.

## Supported versions

Only the latest `main` branch receives security fixes. Releases are cut from
`main`; if you are running an older checkout, update before reporting.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Preferred channels, in order:

1. **GitHub private vulnerability reporting**
   [Security → Report a vulnerability](https://github.com/Exabyte-Technologies/Resona/security/advisories/new)
2. **Email** — fufranky971@gmail.com (Fuhang Fu, co-maintainer)

Please include: a description of the issue, steps to reproduce or a
proof-of-concept, the affected URL(s) or component(s), and your assessment of
impact. Screen recordings or HTTP traces help.

## What to expect

| Step | Target |
|------|--------|
| Acknowledgement | within 72 hours |
| Triage and severity assessment | within 7 days |
| Fix or mitigation for confirmed issues | within 30 days, sooner for actively exploited problems |
| Public disclosure | after the fix ships, credit to the reporter by default |

## Scope

**In scope**

- The web application in this repository (`resona/`, `deploy/`)
- The deployed instance at `https://resona.neuorise.com`
- The Exabyte single sign-on integration and webhook endpoints

**Out of scope**

- Volumetric denial-of-service (please report reflection/amplification bugs,
  not traffic)
- Social engineering of hosting or e-mail providers
- Issues in third-party services themselves (report upstream; we will assist)
- Missing security headers on third-party origins

## Safe harbor

Good-faith research that respects user privacy and service availability will
not be met with legal action. Please avoid automated scanning that degrades
the service for real users, and never access, modify, or exfiltrate data that
is not yours.
