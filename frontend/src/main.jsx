import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
// Imported before App so window.Chart / window.CodeMirror exist by the time any
// component mounts, exactly as they did when index.html loaded them from a CDN
// with plain (blocking) <script> tags. See lib/vendor.js.
import "./lib/vendor.js";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
