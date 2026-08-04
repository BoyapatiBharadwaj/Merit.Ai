/**
 * CodeMirror 5 plus the theme and modes this app uses, published as
 * `window.CodeMirror`.
 *
 * Split out of lib/vendor.js for the same reason as vendorChart.js: imported
 * only by CodeEditor.jsx, so Rollup keeps it inside the exam route's chunk
 * instead of the shared bundle that every visitor downloads.
 *
 * Deep import paths rather than the bare "codemirror" specifier -- CodeMirror 5
 * predates the `exports` field and ships loose UMD files, and the modes and
 * theme are side-effectful modules that register themselves onto the core.
 */
import CodeMirror from "codemirror/lib/codemirror.js";

import "codemirror/lib/codemirror.css";
import "codemirror/theme/dracula.css";
import "codemirror/mode/python/python.js";
import "codemirror/mode/javascript/javascript.js";

window.CodeMirror = CodeMirror;

export default CodeMirror;
