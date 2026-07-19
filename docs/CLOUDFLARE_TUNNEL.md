# Cloudflare Tunnel closed-beta runbook

This runbook is intentionally manual. It does not contain account IDs, tunnel tokens, domains, provider credentials, or Access secrets.

## Before publishing

1. Register an ASCII domain in Cloudflare only after confirming its exact price and name. Use a one-level hostname: `gateway.<domain>`.
2. Set hard spend caps and usage alerts at Groq and 9router.
3. Fill `gateway/.env`, restrict its ACL to the gateway operator, and run `npm.cmd run check` plus `npm.cmd run build:gateway`.
4. Create a remotely managed Cloudflare Tunnel named `cuttoclip-gateway-prod`. Install `cloudflared` as a Windows service and publish `gateway.<domain>` to `http://127.0.0.1:4327`.
5. Create one self-hosted Cloudflare Access application for the hostname. Add a Service Auth policy and a shared service token with a 90-day expiry. Store the client secret when first displayed and rotate it for every tester before expiry.
6. Add one WAF rate-limit rule for `POST /v1/*`: 20 requests per 10 seconds per IP, block with `429` for 10 seconds.
7. Add a Windows Firewall block rule for inbound TCP 4327. The gateway itself binds loopback, so Cloudflare Tunnel is the only intended ingress.

## Test before inviting anyone

1. Run `.\scripts\start-gateway.ps1` and check `http://127.0.0.1:4327/readyz` locally.
2. Verify the public hostname rejects a request without Cloudflare Access headers.
3. Create one smoke invite, activate it using `.\scripts\onboard-tester.ps1`, then send one short audio transcription and one one-window highlight request.
4. Restart the gateway. The installation token must still work and the invite must remain rejected.
5. Invite one tester only, inspect Cloudflare Access logs plus provider usage, then expand the beta.

## Operational limits

- This is a single-host manual gateway. If the Node process stops, the public hostname returns an origin error until the operator runs `scripts/start-gateway.ps1` again.
- The shared Cloudflare service token is a common edge credential. Rotate it immediately if any tester loses a device or the token may have leaked.
- No daily application quota is enforced by choice; provider spend caps are the required cost backstop.
