import "../lib/vendorCodeMirror.js";
import { useEffect, useRef } from "react";

/**
 * Thin React wrapper around CodeMirror 5 (loaded globally via CDN script tags in index.html.
 */
export default function CodeEditor({ value, language, onChange }) {
  const containerRef = useRef(null);
  const cmRef = useRef(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!containerRef.current || !window.CodeMirror) return;
    const cm = window.CodeMirror(containerRef.current, {
      value: value || "",
      mode: language === "javascript" ? "javascript" : "python",
      theme: "dracula",
      lineNumbers: true,
      indentUnit: 4,
      tabSize: 4,
      viewportMargin: Infinity,
    });
    cm.on("change", (instance) => onChangeRef.current?.(instance.getValue()));
    cmRef.current = cm;
    // Defer a refresh to the next frame -- CodeMirror mis-measures itself
    // if created while its container is still animating/hidden.
    requestAnimationFrame(() => cm.refresh());
    return () => {
      cmRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div ref={containerRef} className="cm-host rounded-xl overflow-hidden border border-border" />;
}
