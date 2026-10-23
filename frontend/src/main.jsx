import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import { applyTheme, readCachedTheme } from "./theme.js";
import { ensureSiteTheme } from "./hooks/useSiteTheme.js";
import "./styles.css";

// The theme an admin picked lives in the database, which is one fetch
// away. Paint the last known answer NOW so the first frame is already
// the right colour; useSiteTheme corrects it when the fetch lands.
applyTheme(readCachedTheme());
ensureSiteTheme();

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
