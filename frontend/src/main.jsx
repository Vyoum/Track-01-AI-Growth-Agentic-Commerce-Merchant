import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import A2aPage from "./A2aPage.jsx";
import DemoPage from "./DemoPage.jsx";
import GrowthResultsPage from "./GrowthResultsPage.jsx";
import GuardrailLab from "./GuardrailLab.jsx";
import MerchantPage from "./MerchantPage.jsx";
import "./styles.css";

const ROUTES = {
  "/demo": DemoPage,
  "/growth": GrowthResultsPage,
  "/merchant": MerchantPage,
  "/a2a": A2aPage,
  "/guardrails": GuardrailLab,
};

const path = window.location.pathname.replace(/\/+$/, "") || "/";
const Page = ROUTES[path] || App;

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Page />
  </React.StrictMode>
);
