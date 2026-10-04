# iChancy Official Agent API

The bot now uses the official Bearer-token API only. It does not read, store, send, or request browser cookies, and the manual cookie controls have been removed from both admin dashboards.

## Authentication and token rotation

- `AGENT_USERNAME` and `AGENT_PASSWORD` are used for the official `signIn` endpoint.
- Access and refresh tokens are persisted in `bot_settings` (`ichancy_access_token`, `ichancy_refresh_token`, `ichancy_access_token_expires_at`).
- The access token is renewed shortly before its documented one-hour expiry. The refresh token is rotated on successful refresh; if refresh is expired or rejected, the client signs in again automatically.
- API calls send `Authorization: Bearer <accessToken>`. Session cookies are cleared before and after every HTTP request.
- Existing `ichancy_cookie` / `last_cookie_update` columns on already-deployed databases are not read, written, or dropped. They are inert compatibility data; the application no longer uses them.

## Migrated operations

Documented official endpoints are used for player registration and search, player balance, player deposits and withdrawals, and agent wallet balances. Financial operations do not fall back to legacy cookie endpoints after an API or authentication error.

The supplied API PDF does not describe agent transaction history or player statistics/turnover. To preserve those existing app features, the current client still calls their existing routes with the official Bearer token and without cookies:

- `/global/api/Agent/getAgentTransactionList`
- `/global/api/Statistics/getPlayersStatisticsPro`

These two routes need validation against supplementary iChancy documentation or a live agent account. If they do not accept Bearer-token authentication, those reports/turnover features will need documented official replacements; they will not silently revert to cookies.

## Deployment configuration

Keep these existing environment variables configured:

- `AGENT_USERNAME`
- `AGENT_PASSWORD`
- `PARENT_ID`
- `AGENT_ID`
- `ICHANCY_AGENT_BASE_URL`

The API base URL is the configured agent host. Credentials are never sent to fallback domains.

## Verification and security

Automated tests cover token refresh and rotation, Bearer headers, cookie-free requests, documented endpoint payloads, and registration error handling. A live iChancy transaction test still requires deployment credentials and should be done with a controlled test account.

Never commit credentials or tokens. The previously pasted GitHub access token should be revoked and replaced; it was not used by this migration.