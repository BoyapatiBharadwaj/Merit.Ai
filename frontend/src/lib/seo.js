/**
 * Per-route page metadata.
 *
 * Every route rendered with the single title and description from index.html,
 * so /features, /pricing, /about and /contact shared one search snippet -- a
 * search engine had nothing to distinguish them, and a link shared in a message
 * or a Slack channel previewed as the generic homepage regardless of what was
 * being shared.
 *
 * Deliberately not react-helmet-async. The whole requirement is "set a few tags
 * when the route changes", which is a dozen lines of DOM against a dependency,
 * a provider and a render-phase side-effect model. Setting them directly also
 * means no flash of the previous route's title.
 *
 * This is a client-rendered app, so a crawler that does not execute JavaScript
 * still sees index.html's defaults. Google does execute it; if the marketing
 * pages ever need to rank against competitors, pre-rendering them at build time
 * is the real answer and this is the interface it would keep.
 */

const SITE_NAME = "Merit.Ai";
// Overridden at build time for a real deployment; the sitemap uses the same host.
const SITE_URL = "https://merit.ai";

function upsertMeta(selector, attrs) {
  let el = document.head.querySelector(selector);
  if (!el) {
    el = document.createElement("meta");
    document.head.appendChild(el);
  }
  Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
  return el;
}

function upsertLink(rel, href) {
  let el = document.head.querySelector(`link[rel="${rel}"]`);
  if (!el) {
    el = document.createElement("link");
    el.setAttribute("rel", rel);
    document.head.appendChild(el);
  }
  el.setAttribute("href", href);
}

/**
 * @param {object} meta
 * @param {string} meta.title  - page title, without the site name
 * @param {string} meta.description
 * @param {string} [meta.path] - canonical path; defaults to the current one
 * @param {boolean} [meta.noindex] - for pages behind a login
 */
export function setPageMeta({ title, description, path, noindex = false }) {
  const fullTitle = title ? `${title} · ${SITE_NAME}` : SITE_NAME;
  const url = `${SITE_URL}${path ?? window.location.pathname}`;

  document.title = fullTitle;
  upsertMeta('meta[name="description"]', { name: "description", content: description });

  // Canonical, so the same page reached with a tracking parameter is not
  // treated as a separate, duplicate page.
  upsertLink("canonical", url);

  // noindex is set explicitly rather than omitted: a page behind a login that
  // merely lacks a directive is still crawlable if a URL leaks.
  upsertMeta('meta[name="robots"]', {
    name: "robots",
    content: noindex ? "noindex, nofollow" : "index, follow",
  });

  upsertMeta('meta[property="og:title"]', { property: "og:title", content: fullTitle });
  upsertMeta('meta[property="og:description"]', { property: "og:description", content: description });
  upsertMeta('meta[property="og:url"]', { property: "og:url", content: url });
  upsertMeta('meta[property="og:type"]', { property: "og:type", content: "website" });
  upsertMeta('meta[property="og:site_name"]', { property: "og:site_name", content: SITE_NAME });

  upsertMeta('meta[name="twitter:card"]', { name: "twitter:card", content: "summary_large_image" });
  upsertMeta('meta[name="twitter:title"]', { name: "twitter:title", content: fullTitle });
  upsertMeta('meta[name="twitter:description"]', { name: "twitter:description", content: description });
}

/**
 * What each route says about itself.
 *
 * Descriptions are written to be true rather than to be enticing -- this is a
 * proctoring platform, and a search snippet that oversells what the browser can
 * enforce is the same mistake the Home page copy was making.
 */
export const PAGE_META = {
  "/": {
    title: "Proctored online exams you can trust",
    description:
      "Merit.Ai verifies every candidate against their ID, monitors the session with AI proctoring, " +
      "and records server-authoritative violations — with a complete, timestamped log for review.",
  },
  "/features": {
    title: "Features",
    description:
      "Identity verification, live AI proctoring signals, server-enforced strike handling, " +
      "sandboxed code execution and reviewable violation evidence — including what the platform cannot do.",
  },
  "/pricing": {
    title: "Pricing",
    description:
      "Candidate accounts are free. Institution accounts are priced per organization — request access " +
      "to discuss what your exams need.",
  },
  "/about": {
    title: "About",
    description: "Why Merit.Ai exists, and the principles behind how it handles candidates' data.",
  },
  "/faq": {
    title: "FAQ",
    description:
      "Common questions about sitting a proctored exam on Merit.Ai: what is monitored, what is stored, " +
      "and what happens if something goes wrong mid-exam.",
  },
  "/contact": {
    title: "Contact",
    description: "Get in touch about running proctored exams for your institution.",
  },
  "/privacy": {
    title: "Privacy & proctoring data",
    description:
      "Exactly what Merit.Ai collects during identity verification and proctored exams, what is stored " +
      "rather than discarded, who can see it, and how to have it deleted.",
  },
  "/terms": {
    title: "Terms of Service",
    description:
      "The terms for using Merit.Ai: your account, what is monitored during an exam, what the software " +
      "can and cannot enforce, and how results and retakes are handled.",
  },
  "/system-check": {
    title: "Test your device",
    description:
      "Check your camera, microphone, screen sharing, fullscreen support and connection before exam day. " +
      "No account needed.",
  },
};
