# Human-Bot Verification Upgrade Plan (Deferred)

> **Status: deferred.** This document records the decision and the escalation
> path. Nothing here is scheduled; act only when a trigger in §3 fires.
> Context: security hardening landed in PR #2 (rate limiting, constant-time
> login, single-use CAPTCHA redemption table).

---

## 1. Current state

Resona already runs a **self-hosted proof-of-work CAPTCHA** (`capjs-server`)
on registration, login, and password reset, backed by:

| Layer | Mechanism |
|-------|-----------|
| Challenge | SHA-256 PoW, ~50 challenges, difficulty 4 (client-side compute) |
| Anti-replay | `captcha_redemptions` table stores token SHA-256; unique constraint = single use |
| Retention | redeemed rows pruned after 1 day |
| Rate limiting | per-client + per-identity counters on login, admin login, `/auth/forgot` (PR #2) |
| Enumeration defence | constant-time password check, identical error text (PR #2) |

## 2. Assessment: no upgrade needed today

1. **The threat model does not justify more.** Resona accounts carry no money,
   no email reputation, and no public content ranking worth gaming. PoW raises
   automated-registration cost to "not worth it" for any realistic attacker.
2. **PoW is already the low-friction option.** No third-party script, no
   privacy policy implications, no vendor lock-in — unlike reCAPTCHA/hCaptcha/
   Turnstile, which add an external dependency and visible friction.
3. **CAPTCHA does not stop the attacks that actually matter here.** Credential
   stuffing is answered by rate limiting + PBKDF2 cost, not by harder puzzles.
   Account-takeover defence lives in `session_version` rotation (PR #2).

## 3. Trigger signals (any one fires → re-evaluate)

- Automated signup bursts: many registrations from single IPs or IP ranges
- Garbage usernames / registrations with zero player activity afterwards
- CAPTCHA redemption timing anomalies (solved faster than humanly plausible)
- Registered user count exceeds **20**
- Any observed instance of form spam or scripted abuse

## 4. Escalation ladder

| Level | Action | Notes |
|-------|--------|-------|
| 0 (now) | capjs PoW + rate limiting | current |
| 1 | Add Cloudflare Turnstile **on register + forgot only** | keeps login friction low; requires a CF account |
| 2 | Turnstile everywhere + disposable-email-domain blocklist | protects the Resend sender reputation |
| 3 | Admin approval queue for new signups | last resort; adds operational burden |

Rationale for the ladder: start with the form that is cheapest to abuse
(registration), keep daily login smooth, and only add per-signup human review
when abuse is directed rather than incidental.

## 5. Decision record

- **2026-09-28** — Reviewed after the PR #2 security audit. Decision: keep
  capjs PoW; no migration. Revisit when a §3 trigger fires. Owner: @Franky100-pig
