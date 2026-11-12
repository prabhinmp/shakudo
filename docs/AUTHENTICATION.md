# Authentication: who gets a token, and how

## The short version

There are **no user accounts, no login page and no token endpoint**. A token is a signed string that **you, the platform operator, create** with a
shared secret and hand to a person. The role (`admin`, `operator`, `viewer`) is written *inside* the token, and the signature proves you wrote it.

Think of a hotel with a key-card machine:

| Hotel | This project |
|---|---|
| The card machine | the **signing secret** (only the operator can read it) |
| A key card | a **token** |
| "This card opens the gym only" / "opens every room" | the token's **role** and **namespaces** |
| The front desk | **you**: whoever can run `kubectl` against the cluster |
| A guest scribbling on a card to open more doors | editing a token: the signature no longer matches, so the door rejects it (`401`) |

## Two separate logins, don't mix them up

| Hop | How it authenticates |
|---|---|
| **Person or script → this API** | a signed **JWT** in `Authorization: Bearer <token>` (this page) |
| **This API → Kubernetes** | the API pod's own **ServiceAccount token**, limited by RBAC (a ClusterRole with only the verbs it needs) |

People never get Kubernetes credentials through this API. They only get a token for the API.

## What is inside a token

| Claim | Meaning |
|---|---|
| `role` | `viewer` (read) < `operator` (+ patch, restart) < `admin` (+ create, delete) |
| `namespaces` | namespaces this token may use, e.g. `["team-a-*"]` or `["*"]` (every namespace except the protected built-in ones) |
| `sub` | a **name label** for the audit log. It is not checked against any user database |
| `exp` | expiry time (default 1 hour) |
| `iss`, `aud` | must be `deploy-api` (configurable) |

Example (decoded), with a dummy secret:

```json
{ "sub": "carol", "iss": "deploy-api", "aud": "deploy-api", "exp": "2026-11-12 23:11:47", "role": "viewer", "namespaces": ["team-a-*"] }
```

| Role | May call |
|---|---|
| viewer | `GET` list, detail, namespaces |
| operator | + `PATCH` (scale, image), restart |
| admin | + `POST` create, `DELETE` |

## Where the secret lives

| Item | Value |
|---|---|
| Kubernetes Secret | `deploy-api-deploy-api-jwt` in namespace `deploy-api` (name is `<release>-deploy-api-jwt`), key `jwt-secret` |
| Created by | the Helm chart (`randAlphaNum 48`), kept across upgrades and uninstalls (`helm.sh/resource-policy: keep`) |
| Bring your own | `helm upgrade ... --set jwt.existingSecret=<secret-name>` |
| The API refuses to start | if the secret is missing or shorter than 32 bytes |

**Whoever can read this Secret can create an admin token.** Restrict `kubectl` access to the `deploy-api` namespace accordingly.

## How to give someone a token

You (the secret holder) mint one token per person and send that person **only their token**, never the secret.

```bash
export SECRET=$(kubectl -n deploy-api get secret deploy-api-deploy-api-jwt -o jsonpath='{.data.jwt-secret}' | base64 -d)

#                       who             role      namespaces        lifetime (seconds)
JWT_SUB=alice ./scripts/mint-token.sh admin    '["*"]'             28800    # 8 hours, everything
JWT_SUB=bob   ./scripts/mint-token.sh operator '["team-a-*"]'      28800    # scale/restart in team-a-*
JWT_SUB=carol ./scripts/mint-token.sh viewer   '["team-a-*"]'      86400    # read-only, 24 hours
```

[scripts/mint-token.sh](../scripts/mint-token.sh) arguments and variables:

| Input | Default | Meaning |
|---|---|---|
| `SECRET` (env) | required | the signing secret |
| 1st argument | required | `viewer`, `operator` or `admin` |
| 2nd argument | `["*"]` | namespaces claim as a JSON list |
| 3rd argument | `3600` | lifetime in seconds |
| `JWT_SUB` (env) | `tester` | name shown in the audit log |
| `JWT_ISSUER`, `JWT_AUDIENCE` (env) | `deploy-api` | must match the API's settings |

The person then uses it:

```bash
curl -s $BASE/api/deployments -H "Authorization: Bearer <their token>"
```

## What a token holder cannot do

Verified with a test instance and a throwaway secret:

| Attempt | Result |
|---|---|
| viewer token creating a deployment | `403` role too low |
| viewer edits their token to say `role: admin` (old signature kept) | `401` signature no longer matches |
| viewer signs a new token with a guessed secret | `401` |
| token with no `namespaces` list, or a role other than the three | `401` |
| expired token, wrong issuer/audience, `alg: none` token | `401` |
| any token, any role, using `kube-system`, `kube-public`, `kube-node-lease`, `default` | `403` |

The same cases are covered by `tests/test_security.py`.

## Limits you should know about

- **No revocation.** A token works until it expires. Use short lifetimes. The only way to cancel tokens early is to replace the secret, which invalidates
  **all** tokens (not exercised in this project; after replacing the Secret the API pods must be restarted to read it).
- **No user accounts.** `sub` is only a label. Anyone holding a token is treated as that role.
- **Anyone who can read the Secret is effectively an admin.**
- **Brute-force protection.** More than 10 failed authentications per minute from one client IP return `429` for a minute. Behind the ingress all clients share
  the ingress pod's IP, so this is coarse.
- **Rate limits** are per token subject (120 reads and 30 writes per minute) and counted per pod.

## Production direction

A shared secret is fine for development and for a small trusted team. For real users, put an identity provider (OIDC, e.g. Keycloak, Okta or Google) in front
so people sign in and receive short-lived tokens, and have the API verify them with the provider's public keys (RS256 / JWKS) instead of a shared secret.
This is not implemented yet.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `401 Missing bearer token` | no `Authorization: Bearer ...` header |
| `401 Invalid or expired token` | wrong secret, expired, or tampered; mint a new one with the secret from the **same** cluster |
| `403 Role 'viewer' may not perform this action` | the token's role is too low for that endpoint |
| `403 ... is not in this token's allowed namespaces (...)` | the token's `namespaces` claim does not include it; mint one that does, or use `*` |
| `403 ... is protected (built-in Kubernetes namespace)` | `kube-system`, `kube-public`, `kube-node-lease`, `default` and `kube-*` can never be used |
| `429` right after a few `401`s | repeated failed auth; wait a minute |
