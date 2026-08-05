/**
 * Session storage, using the same localStorage keys as the legacy
 * frontend/js/auth.js so a token issued here is structurally compatible
 * with the rest of the platform once everything shares one origin.
 *
 * "Remember me" (Login.jsx) is implemented as a choice of *which* storage
 * holds the session, not a separate flag read alongside it: localStorage
 * survives closing the browser, sessionStorage clears the moment the tab
 * does, and that difference is exactly what the checkbox promises on a
 * platform that may well be opened on a shared/lab machine between exams.
 * REMEMBER_KEY itself always lives in localStorage regardless of the choice
 * it records -- it has to sit somewhere fixed and findable so every other
 * getter knows which storage to check, without probing both on every read.
 */
const TOKEN_KEY = "aep_token";
const ROLE_KEY = "aep_role";
const NAME_KEY = "aep_name";
const USER_ID_KEY = "aep_user_id";
const REMEMBER_KEY = "aep_remember";
// Whether this account is holding a password its owner never chose (an admin
// reset, or an account created for them). Kept alongside the session because it
// is a property of the session, and cleared the moment they set their own.
const MUST_CHANGE_KEY = "aep_must_change_password";
const SESSION_KEYS = [TOKEN_KEY, ROLE_KEY, NAME_KEY, USER_ID_KEY, MUST_CHANGE_KEY];

function activeStorage() {
  return localStorage.getItem(REMEMBER_KEY) === "0" ? sessionStorage : localStorage;
}

// `remember = true` by default so every other existing call site (Register.jsx,
// and anything else that logs a session in without an explicit choice) keeps
// today's behavior -- only Login.jsx's unchecked box opts into the
// session-only path.
export function setSession(data, remember = true) {
  localStorage.setItem(REMEMBER_KEY, remember ? "1" : "0");
  const store = remember ? localStorage : sessionStorage;
  const other = remember ? sessionStorage : localStorage;
  // Clear the storage NOT chosen this time, so switching "remember me" off
  // (or on) on a shared machine can't leave a stale, readable copy of the
  // previous session sitting in the other one.
  SESSION_KEYS.forEach((key) => other.removeItem(key));
  store.setItem(TOKEN_KEY, data.access_token);
  store.setItem(ROLE_KEY, data.role);
  store.setItem(NAME_KEY, data.full_name);
  store.setItem(USER_ID_KEY, String(data.user_id));
  if (data.must_change_password) store.setItem(MUST_CHANGE_KEY, "1");
  else store.removeItem(MUST_CHANGE_KEY);
}

/**
 * Is this account signed in on a password somebody else chose?
 *
 * Surfaced rather than enforced with a redirect, deliberately. Students cannot
 * change their own password on this platform (users.change_my_password refuses
 * them by design), so forcing every flagged session to a change-password screen
 * would strand exactly the role that has no form to fill in. A notice that
 * names the right route for each role is the honest version.
 */
export function mustChangePassword() {
  return activeStorage().getItem(MUST_CHANGE_KEY) === "1";
}

/** Called once the owner has set their own password. */
export function clearMustChangePassword() {
  [localStorage, sessionStorage].forEach((store) => store.removeItem(MUST_CHANGE_KEY));
}

export function clearSession() {
  localStorage.removeItem(REMEMBER_KEY);
  [localStorage, sessionStorage].forEach((store) => SESSION_KEYS.forEach((key) => store.removeItem(key)));
}

export function getToken() {
  return activeStorage().getItem(TOKEN_KEY);
}

export function getRole() {
  return activeStorage().getItem(ROLE_KEY);
}

export function getName() {
  return activeStorage().getItem(NAME_KEY);
}

/**
 * Read the expiry out of a JWT without verifying it.
 *
 * Verification is the server's job and always will be -- this is only to avoid
 * navigating somebody to a page that is about to bounce them. A forged token
 * that lies about its expiry gains nothing: every request is still checked
 * server-side.
 */
function tokenExpiry(token) {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return typeof payload.exp === "number" ? payload.exp * 1000 : null;
  } catch {
    // Not a JWT we can read. Treat it as having no expiry rather than as
    // invalid: the server is the authority, and locking someone out over an
    // unparseable local string would be the wrong failure.
    return null;
  }
}

/**
 * Is there a session worth acting on?
 *
 * This used to be `!!getToken()` -- true for a token that expired two hours
 * ago. So Login redirected to Dashboard, Dashboard fetched, the API returned
 * 401, the session was cleared and the person landed back at Login having been
 * bounced through two screens for nothing. Checking the expiry we already hold
 * turns that into a normal, immediate "please sign in".
 */
export function isLoggedIn() {
  const token = getToken();
  if (!token) return false;

  const expiresAt = tokenExpiry(token);
  if (expiresAt !== null && Date.now() >= expiresAt) {
    // Clear it on the way out. Leaving a token known to be dead in storage
    // means every subsequent check pays the same round trip to rediscover it.
    clearSession();
    return false;
  }
  return true;
}

/** Milliseconds until the session expires, or null if it does not/cannot say. */
export function sessionExpiresIn() {
  const token = getToken();
  if (!token) return null;
  const expiresAt = tokenExpiry(token);
  return expiresAt === null ? null : Math.max(0, expiresAt - Date.now());
}

export function roleLabel(role) {
  if (role === "admin") return "Admin";
  if (role === "examiner") return "Examiner";
  return "Student";
}
