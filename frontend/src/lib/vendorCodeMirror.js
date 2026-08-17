/**
 * CodeMirror 5 plus the theme and modes this app uses, published as `window.CodeMirror`.
 */
import CodeMirror from "codemirror/lib/codemirror.js";

import "codemirror/lib/codemirror.css";
import "codemirror/theme/dracula.css";
import "codemirror/mode/python/python.js";
import "codemirror/mode/javascript/javascript.js";

window.CodeMirror = CodeMirror;

export default CodeMirror;
