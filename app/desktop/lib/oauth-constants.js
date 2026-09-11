'use strict';
// Shared between lib/auth.js, which builds the Supabase authorize and PKCE token-exchange requests, and
// main.js, which registers a matching custom protocol so the system browser can hand the OAuth redirect back
// to this desktop process instead of to a web page.
module.exports = {
  OAUTH_REDIRECT_PATH: 'oauth-callback',
  OAUTH_DEFAULT_PROVIDER: 'google',
  PKCE_GRANT_TYPE: 'pkce',
  PKCE_CHALLENGE_METHOD: 's256',
  PKCE_VERIFIER_BYTES: 32,
  OAUTH_STATE_BYTES: 16,
};
