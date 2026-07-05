# Authentication & JWT

_7 finding(s) in this dimension._

Findings on the auth lifecycle: JWT secret fail-fast, token revocation (`token_version`), refresh-token rotation, login enumeration (timing + inactive-status leakage), and the bcrypt 72-byte truncation collision. Fixed by adding `model_post_init` fail-fast for insecure secrets, embedding `ver` in tokens and checking it on every request + refresh, adding `/auth/logout` (bumps `token_version`), rotating refresh tokens, timing-equalizing login against a dummy hash, capping passwords at 72 bytes, and using a generic anti-enumeration register message.

---

### `default-jwt-secret-no-fail-fast`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | secret |
| Location | `backend/app/core/config.py:74` |
| Status | **Fixed** |

**Summary.** jwt_secret defaults to the publicly-known string "change-me-in-prod" and no validator rejects it when environment != dev, so any deployment that forgets to set ATHENA_JWT_SECRET silently runs with a forgeable secret.

**Failure scenario.** Operator deploys without setting ATHENA_JWT_SECRET (or a CI/compose misconfiguration drops the env var). jwt_secret stays "change-me-in-prod". Attacker reads this default from the public repo, then forges HS256 JWTs locally: jwt.encode({"sub":"<victim uuid>","type":"access","exp":...}, "change-me-in-prod", algorithm="HS256") and calls any CurrentUserId/DbSession-protected endpoint as that user. They can likewise forge refresh tokens (type="refresh") valid for 14 days.

**Evidence.** jwt_secret: str = "change-me-in-prod"
jwt_algorithm: str = "HS256" # config.py:74-75 " no @field_validator rejecting the default when environment != 'dev'

**Suggested fix.** Add a @field_validator("jwt_secret") that raises if value == "change-me-in-prod" and settings.environment != "dev" (or always, for prod). E.g.:
@field_validator("jwt_secret")
@classmethod
 def _reject_default(cls, v, info):
 env = info.data.get("environment", "dev")
 if v == "change-me-in-prod" and env != "dev":
 raise ValueError("ATHENA_JWT_SECRET must be set to a strong value in non-dev environments")
 return v
Also enforce a minimum length (e.g. >= 32 bytes).

**Verification rationale.** Confirmed by reading backend/app/core/config.py:74 " `jwt_secret: str = "change-me-in-prod"` with `jwt_algorithm: str = "HS256"` (line 75). The only @field_validator in the file is `_ensure_path` for `storage_dir` (lines 82-85); there is NO validator on jwt_secret, and no other check anywhere in the backend (grep across backend/ found only conftest.py setting a test secret, config.py defaulting it, and security.py using it). backend/app/core/security.py uses `_settings.jwt_secret` directly for both jwt.encode (line 49) and jwt.decode (line 67). create_access_token (line 52-56) emits `{"sub": str(user_id), "type": "access"}` and create_refresh_token (line 59-63) emits `{"sub": str(user_id), "type": "refresh"}` valid for 14 days " exactly the claims an attacker can forge with the publicly-known default. Any deployment that forgets ATHENA_JWT_SECRET silently runs with a forgeable secret, and `environment` (line 23, default "dev") does not gate this. The suggested fix (a @field_validator rejecting the default when environment != "dev" plus a min length) is appropriate. Severity high is correct: trivial, unauthenticated, full account impersonation via token forgery, gated only on an env var being remembered.

**Notes.** File/line accurate: backend/app/core/config.py line 74 (jwt_secret) and line 75 (jwt_algorithm). No off-by-one. No mitigation found elsewhere in backend/. Tests/conftest.py:20 only sets a test secret, not a runtime guard.


---

### `login-json-missing-is-active-check`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | logic-bug |
| Location | `backend/app/api/auth.py:95` |
| Status | **Fixed** |

**Summary.** login_json authenticates purely on email+password and issues access+refresh tokens without ever checking user.is_active, unlike /auth/login which checks it at auth.py:88-91.

**Failure scenario.** An admin disables a user (is_active=False) to revoke access. The user can still POST to /auth/login-json with their known password and receive a valid access token. That token passes get_current_user_id (deps.py:25-47) because that dependency only checks the JWT type claim, not is_active. Any route protected by CurrentUserId or DbSession (rather than CurrentUser) " both exported as the standard dependency aliases in dependencies.py:17,37 " will accept the disabled user's token. /auth/login correctly blocks them with 403 'Inactive user', so the bypass is specifically via /auth/login-json.

**Evidence.** # auth.py:95-105 " no is_active branch anywhere in login_json
@router.post("/login-json", response_model=TokenPair)
async def login_json(payload: UserLogin, session: AnonDbSession) -> TokenPair:
 res = await session.execute(select(User).where(User.email == payload.email))
 user = res.scalar_one_or_none()
 if not user or not verify_password(payload.password, user.password_hash):
 raise HTTPException(...)
 return _make_pair(user.id)
# Compare auth.py:88-91 in /auth/login which DOES check: if not user.is_active: raise 403

**Suggested fix.** Add the same is_active check to login_json after the password check:
if not user.is_active:
 raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
Ideally also make get_current_user_id (deps.py) do a DB lookup + is_active check, or mandate that all protected routes use CurrentUser rather than CurrentUserId, so a stale token issued to a since-disabled user is also rejected at the gate.

**Verification rationale.** Confirmed in the actual code. backend/app/api/auth.py:95-105 (login_json) authenticates on email+password and calls _make_pair(user.id) with no is_active check, while /auth/login at lines 88-91 does check `if not user.is_active: raise 403`. backend/app/core/deps.py:25-47 (get_current_user_id) only validates the JWT type claim and returns the sub UUID " no DB lookup, no is_active check. backend/app/api/dependencies.py:17 (CurrentUserId) and line 37 (DbSession via get_user_db, which only sets the RLS GUC) both rely on get_current_user_id and do not check is_active; only CurrentUser (line 58, get_current_user) checks is_active at lines 47-54. Therefore a disabled user (is_active=False) can POST /auth/login-json with a known password, receive a valid access token, and access any route protected by CurrentUserId or DbSession until the token expires. The /refresh endpoint (lines 137-143) does check is_active, but that does not mitigate the already-minted access token. The bypass is specifically via /auth/login-json as claimed.

**Notes.** Line citation is essentially correct: @router.post("/login-json") decorator is line 95, async def login_json is line 96. The missing is_active check should be inserted after the password check (after line 104) and before `return _make_pair(user.id)` on line 105. Suggested fix in the claim is correct.


---

### `refresh-token-reuse-no-rotation-revocation`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | auth |
| Location | `backend/app/api/auth.py:108` |
| Status | **Fixed** |

**Summary.** The /auth/refresh endpoint accepts the same refresh token repeatedly and only mints a new access token; there is no jti, no server-side revocation list, and no rotation, so a stolen refresh token is reusable for the full 14-day TTL even after the user changes their password.

**Failure scenario.** Attacker exfiltrates a victim's refresh token (e.g. from a leaked localStorage in an SPA). For the entire refresh_token_ttl_days=14 window they can POST /auth/refresh repeatedly to mint fresh access tokens at will. The victim changing their password does NOT invalidate the old refresh token (no password_changed_at check, no token version), so the compromise persists for up to 14 days regardless of remediation. There is also no /auth/logout endpoint to revoke it.

**Evidence.** # auth.py:108-148: decode -> check type=="refresh" -> lookup user -> create_access_token. No jti, no denylist, no rotation:
return AccessToken(access_token=create_access_token(user.id), token_type="bearer", expires_in=...)
# No new refresh_token issued, no invalidation of the presented one.

**Suggested fix.** Implement refresh-token rotation: on each /auth/refresh, issue a fresh refresh_token and invalidate (jti denylist in Redis) the presented one. Add a token_version or password_changed_at column on User and embed it in the token; reject tokens whose version is stale. Provide a /auth/logout that denylists the current refresh jti.

**Verification rationale.** Confirmed in the actual code. auth.py:108-148 `/auth/refresh` only decodes the token, verifies `type=="refresh"`, looks up the user, checks `is_active`, and mints a new access token via `create_access_token(user.id)`. No new refresh token is issued (no rotation), the presented refresh token is never invalidated, and there is no jti or server-side denylist. security.py:52-67 shows the JWT payload contains only `sub`, `type`, `iat`, `exp` " no `jti` and no token version. models/user.py:14-25 shows the User table has no `password_changed_at` or `token_version` column, so a password change cannot invalidate outstanding refresh tokens. A grep for logout/revoke/jti/token_version/password_changed across backend/app found zero application matches (only third-party .venv files), and the router in auth.py only exposes /register, /login, /login-json, /refresh, /me " there is no /auth/logout. Thus a stolen refresh token is reusable for the entire refresh_token_ttl_days=14 window even after the victim changes their password, exactly as described.


---

### `bcrypt-72-byte-truncation-collision`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | high |
| Category | auth |
| Location | `backend/app/core/security.py:21` |
| Status | **Fixed** |

**Summary.** _normalize slices every password to the first 72 bytes before bcrypt, so any two passwords with identical first 72 bytes produce the same hash and authenticate as each other.

**Failure scenario.** User picks a long passphrase (>72 bytes). Another party who knows only the first 72 bytes of that passphrase can authenticate as the user, because the trailing bytes are dropped before hashing. The UserCreate schema (schemas/auth.py:14) allows up to 128 chars, so a user may legitimately create a password whose tail is ignored.

**Evidence.** _BCRYPT_MAX_BYTES = 72
def _normalize(plain: str) -> bytes:
 raw = plain.encode("utf-8")
 return raw[:_BCRYPT_MAX_BYTES] # silent truncation; bytes beyond 72 are discarded

**Suggested fix.** Either pre-hash the password with SHA-256 before bcrypt (so the full input contributes) and document it, or reject passwords whose UTF-8 length exceeds 72 bytes with a 422 (UserCreate validator) so the truncation is never silently applied.

**Verification rationale.** Verified in the actual code: backend/app/core/security.py lines 18-23 define _BCRYPT_MAX_BYTES=72 and _normalize(plain) returns plain.encode('utf-8')[:72], and both hash_password (line 28) and verify_password (line 33) feed _normalize(plain) into bcrypt. backend/app/schemas/auth.py line 14 sets password max_length=128 with no byte-length check, so a user may create a passphrase >72 UTF-8 bytes whose tail is silently dropped. Any two passwords sharing the first 72 UTF-8 bytes hash identically and authenticate as each other. No pre-hashing, no validator rejecting >72-byte passwords, and UserLogin (line 18-19) has no length cap, so the collision is exploitable as described.

**Notes.** File/line match exactly: security.py:21 (_normalize) with _BCRYPT_MAX_BYTES at line 18; schemas/auth.py:14 (UserCreate.password max_length=128). Severity low confirmed. Suggested fix (SHA-256 pre-hash or 422 on >72 UTF-8 bytes) is valid.


---

### `login-reveals-inactive-status`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | high |
| Category | auth |
| Location | `backend/app/api/auth.py:88` |
| Status | **Fixed** |

**Summary.** When the password is correct but is_active is False, /auth/login returns a distinct 403 'Inactive user', confirming both that the email is registered and that the password is correct.

**Failure scenario.** Attacker submits a candidate email/password pair. A 403 'Inactive user' response confirms the email is registered AND the password is the right one (only the account is administratively disabled) " useful for credential-stuffing triage and for re-using that password elsewhere.

**Evidence.** if not user.is_active:
 raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
# runs only AFTER verify_password succeeded at line 82

**Suggested fix.** Fold the inactive check into the generic 401 'Incorrect email or password' so as not to distinguish the cases, or short-circuit it before verify_password runs (still returns 401).

**Verification rationale.** Read backend/app/api/auth.py lines 74-92. The login endpoint at line 82 first checks `if not user or not verify_password(...)` and raises 401 'Incorrect email or password'. Only AFTER verify_password succeeds does line 88 `if not user.is_active:` raise a distinct 403 'Inactive user' (lines 89-91). This confirms the claim: a 403 response leaks that the email is registered AND the supplied password is correct, only the account is administratively disabled " a credential-stuffing triage oracle. Line 88 is accurate. Severity low is appropriate (information disclosure, not auth bypass).

**Notes.** Line/line number (88) and file path are accurate. The /auth/login-json endpoint at lines 95-105 lacks the is_active check entirely, which is a separate (more severe) issue not covered by this claim.


---

### `login-timing-email-enumeration`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | high |
| Category | auth |
| Location | `backend/app/api/auth.py:80` |
| Status | **Fixed** |

**Summary.** verify_password (bcrypt, ~tens of ms) only runs when the user row exists; the missing-user branch returns immediately, so login latency differs by whether the email is registered, enabling timing-based email enumeration.

**Failure scenario.** Attacker measures response time of POST /auth/login (and /auth/login-json) for many candidate emails. Addresses with a registered account take measurably longer (bcrypt runs) than unregistered ones (immediate 401). The slow set is the registered-email list. No rate limiting amplifies the feasibility.

**Evidence.** # auth.py:80-82 (login) and 98-100 (login-json) share the same structure:
user = res.scalar_one_or_none()
if not user or not verify_password(form_data.password, user.password_hash):
 raise HTTPException(... 401 ...)
# When user is None, the `or` short-circuits and verify_password is NOT called -> fast.

**Suggested fix.** Always run a bcrypt comparison against a fixed dummy hash when the user is None, so both branches take comparable time:
DUMMY_HASH = hash_password("dummy")
if user is None:
 verify_password(form_data.password, DUMMY_HASH) # burn time
 raise 401

**Verification rationale.** Verified in Y:\AI_Projects\project-athena\backend\app\api\auth.py and Y:\AI_Projects\project-athena\backend\app\core\security.py. The login handler at lines 80-82 and login-json at 98-100 both use the pattern `if not user or not verify_password(...)`. Python's `or` short-circuits, so when `user` is None (falsy) the second operand is never evaluated and verify_password is skipped, returning an immediate 401. When the user row exists, `verify_password` (security.py:31-35) runs `bcrypt.checkpw` " by default bcrypt uses a work factor of 12, which takes tens of milliseconds per call. That latency delta (immediate vs ~tens of ms) is measurable over many requests, enabling timing-based email enumeration against both POST /auth/login and POST /auth/login-json. I also grepped the backend app code for rate-limiting middleware (slowapi/Limiter/throttle) and found nothing in application code " only venv matches " so there is no rate limiting to dampen the attack. The finding's suggested fix (run bcrypt against a fixed dummy hash on the missing-user branch) is the standard mitigation. The cited file/line numbers are exact.

**Notes.** File/line accurate: auth.py:80-82 (login) and 98-100 (login-json). verify_password at security.py:31-35 uses bcrypt.checkpw. Both endpoints use the short-circuiting `or` pattern, so the missing-user branch returns immediately while the existing-user branch pays the bcrypt cost. No app-level rate limiting found.


---

### `register-email-enumeration`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | high |
| Category | auth |
| Location | `backend/app/api/auth.py:62` |
| Status | **Fixed** |

**Summary.** /auth/register returns 400 'Email already registered' for an existing email, confirming account existence to an unauthenticated caller.

**Failure scenario.** Attacker scripts POST /auth/register with a candidate email list. A 400 'Email already registered' response marks that address as a registered account; 201 marks it as unused. The resulting account list feeds targeted phishing or login brute-force (no rate limit on /auth/login).

**Evidence.** existing = await session.execute(select(User).where(User.email == payload.email))
if existing.scalar_one_or_none() is not None:
 raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

**Suggested fix.** Return the same 201 response (or a generic 'check your email to confirm' message) regardless of whether the email exists, and send a confirmation email to a pre-existing address instead of inlining the duplicate check.

**Verification rationale.** Confirmed in Y:\AI_Projects\project-athena\backend\app\api\auth.py lines 59-66. The register endpoint, on the unauthenticated AnonDbSession, executes `select(User).where(User.email == payload.email)` and raises HTTPException(400, "Email already registered") when an existing user is found, while returning 201 for unused emails. This lets an unauthenticated caller distinguish registered vs. unregistered emails purely by status code (400 vs 201), enabling account enumeration. No mitigation is present in the file: there is no email-confirmation flow, no uniform response, and no rate limiting visible on /auth/register. Contrast with the login endpoint (lines 80-87) which correctly returns a generic 401 regardless of email existence. The claimed line (62) is accurate; the evidence snippet matches the actual code verbatim.

**Notes.** File/line (auth.py:62) confirmed correct. The failure scenario's additional claim of "no rate limit on /auth/login" could not be verified from auth.py alone " no rate limiting is visible anywhere in this file, but a global middleware/ratelimit dependency could exist elsewhere. The core enumeration vulnerability stands regardless. Severity low is appropriate: email-existence disclosure is a minor info leak, not direct account compromise.


---
