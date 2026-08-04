import { useEffect, useRef } from "react";

/**
 * Thin React wrapper around CodeMirror 5 (loaded globally via CDN script
 * tags in index.html -- window.CodeMirror -- same build the legacy
 * frontend/exam.html used). Mounted fresh (via a `key` on the parent) each
 * time the current coding question changes, so `value` only needs to be
 * the correct *initial* source for that question -- no imperative
 * setValue()/suppress-flag dance needed, since there's no "reuse across
 * questions" requirement once React owns the mount/unmount lifecycle.
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
