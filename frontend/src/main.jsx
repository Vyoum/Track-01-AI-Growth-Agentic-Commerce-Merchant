import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import A2aPage from "./A2aPage.jsx";
import MerchantPage from "./MerchantPage.jsx";
import "./styles.css";

const path = window.location.pathname.replace(/\/+$/, "") || "/";
const Page =
  path === "/merchant" ? MerchantPage : path === "/a2a" ? A2aPage : App;

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Page />
  </React.StrictMode>
);
