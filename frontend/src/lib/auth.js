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
const SESSION_KEYS = [TOKEN_KEY, ROLE_KEY, NAME_KEY, USER_ID_KEY];

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

export function isLoggedIn() {
  return !!getToken();
}

export function roleLabel(role) {
  if (role === "admin") return "Admin";
  if (role === "examiner") return "Examiner";
  return "Student";
}
