/**
 * Globally-needed third-party assets, bundled from node_modules rather than
 * fetched from a CDN.
 *
 * Only fonts live here now. Chart.js and CodeMirror moved to vendorChart.js and
 * vendorCodeMirror.js so they land in the route chunks that actually use them
 * -- see those files. This module is imported by main.jsx and is therefore part
 * of the bundle every visitor downloads, so anything added here should be
 * something every page genuinely needs.
 *
 * The reason all of this is vendored at all: these were <script> and <link>
 * tags pointing at cdnjs and Google Fonts with no Subresource Integrity. The
 * exam page holds camera, microphone and screen-share permissions plus the
 * user's token, so a compromise of any of those origins meant arbitrary code in
 * the most privileged context in the product.
 *
 * `latin-<weight>.css`, not `<weight>.css`: the unsubsetted entry point pulls
 * in cyrillic, greek, vietnamese and latin-ext for every weight -- roughly 60
 * font files where 6 are used. Google Fonts only ever served the subset the
 * browser asked for, so importing everything would make self-hosting a
 * regression in payload rather than just a change of origin.
 */
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/inter/latin-700.css";
import "@fontsource/inter/latin-800.css";
import "@fontsource/inter/latin-900.css";
