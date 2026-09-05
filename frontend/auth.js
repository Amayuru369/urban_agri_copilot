/* ---------------------------------------------------------------------------
 * UrbanAgri-Copilot — Shared Authentication Helpers
 *
 * Loaded by both frontend/index.html and frontend/garden.html so that JWT
 * storage, decoding, session-refresh, and logout logic live in exactly one
 * place. Exposes a small, stable global surface:
 *
 *   TOKEN_STORAGE_KEY   — localStorage key holding the JWT
 *   PROFILE_STORAGE_KEY — localStorage key holding the admin's active scope
 *   getAuthToken()      — read the raw JWT (or null)
 *   clearAuthToken()    — remove the JWT from storage
 *   decodeTokenPayload(token) — client-side HS256 payload decode (no verify)
 *   redirectToLogin()   — clear token and bounce to /login.html
 *   logoutAndRedirect() — clear token + profile scope and bounce to login
 *   fetchCurrentUser()  — GET /api/users/me with Bearer, falls back to token
 *                         claims on network failure; auto-redirects on 401
 *   isAdminUser(user)   — helper predicate
 *
 * Additive: this file only attaches globals; it never mutates the DOM.
 * ------------------------------------------------------------------------ */
(function (global) {
  'use strict';

  var TOKEN_STORAGE_KEY = 'urbanagri_token';
  var PROFILE_STORAGE_KEY = 'urbanagri_active_user_id';

  function getAuthToken() {
    try {
      return global.localStorage.getItem(TOKEN_STORAGE_KEY);
    } catch (_) {
      return null;
    }
  }

  function clearAuthToken() {
    try {
      global.localStorage.removeItem(TOKEN_STORAGE_KEY);
    } catch (_) {
      /* ignore (private mode / storage disabled) */
    }
  }

  /**
   * Decode the JWT payload segment WITHOUT verifying the signature. This is
   * purely a client-side convenience so pages can render name/role without a
   * round-trip; the server is always the source of truth.
   */
  function decodeTokenPayload(token) {
    if (!token) return null;
    try {
      var parts = String(token).split('.');
      if (parts.length < 2) return null;
      var payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      while (payload.length % 4) payload += '=';
      var json = decodeURIComponent(
        atob(payload)
          .split('')
          .map(function (c) {
            return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
          })
          .join('')
      );
      return JSON.parse(json);
    } catch (_) {
      return null;
    }
  }

  function redirectToLogin() {
    clearAuthToken();
    global.location.replace('/login.html');
  }

  function logoutAndRedirect() {
    clearAuthToken();
    try {
      global.localStorage.removeItem(PROFILE_STORAGE_KEY);
    } catch (_) {
      /* ignore */
    }
    global.location.replace('/login.html');
  }

  /**
   * Fetch the authenticated profile from /api/users/me.
   * - No token → null (caller decides whether to redirect).
   * - 401 → clears the token and redirects to /login.html.
   * - Network/other failure → falls back to decoding the token claims so the
   *   UI can still render name + role in degraded mode.
   */
  async function fetchCurrentUser() {
    var token = getAuthToken();
    if (!token) return null;

    try {
      var res = await fetch('/api/users/me', {
        headers: { Authorization: 'Bearer ' + token },
      });
      if (res.status === 401) {
        redirectToLogin();
        return null;
      }
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (err) {
      if (global.console && console.warn) {
        console.warn('[auth] /api/users/me failed; falling back to token claims:', err);
      }
      var claims = decodeTokenPayload(token);
      if (!claims) return null;
      return {
        id: claims.sub,
        name: claims.name,
        is_admin: Boolean(claims.is_admin),
        telegram_chat_id: claims.telegram_chat_id || null,
      };
    }
  }

  function isAdminUser(user) {
    return Boolean(user && user.is_admin);
  }

  // Expose the API as globals so classic <script> pages can use them directly.
  global.TOKEN_STORAGE_KEY = TOKEN_STORAGE_KEY;
  global.PROFILE_STORAGE_KEY = PROFILE_STORAGE_KEY;
  global.getAuthToken = getAuthToken;
  global.clearAuthToken = clearAuthToken;
  global.decodeTokenPayload = decodeTokenPayload;
  global.redirectToLogin = redirectToLogin;
  global.logoutAndRedirect = logoutAndRedirect;
  global.fetchCurrentUser = fetchCurrentUser;
  global.isAdminUser = isAdminUser;
})(window);
