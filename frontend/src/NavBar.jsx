import { useCallback, useEffect, useRef, useState } from "react";
import { fetchHealth, searchProducts } from "./api.js";

const PAGES = [
  { href: "/demo", label: "Judge Mode" },
  { href: "/growth", label: "Growth Results" },
  { href: "/", label: "Checkout" },
  { href: "/merchant", label: "Merchant" },
  { href: "/a2a", label: "A2A" },
  { href: "/guardrails", label: "Guardrails" },
];

export default function NavBar({ current, children }) {
  const [sourceStatus, setSourceStatus] = useState(null);
  const [retrying, setRetrying] = useState(false);
  const [retryMessage, setRetryMessage] = useState("");
  const retryInFlight = useRef(false);

  const retryLiveSource = useCallback(async () => {
    if (retryInFlight.current) return;
    retryInFlight.current = true;
    setRetrying(true);
    setRetryMessage("");
    try {
      // A catalog operation updates the backend's actual source provenance.
      // It may return mock products, so health remains the authoritative result.
      await searchProducts("");
      const health = await fetchHealth();
      setSourceStatus(health);
      setRetryMessage(
        health.catalog_fallback_active
          ? "Live merchant is still unavailable; mock catalog remains active."
          : "Live merchant connection restored."
      );
    } catch (error) {
      setRetryMessage(error?.message || "Live merchant retry failed.");
    } finally {
      retryInFlight.current = false;
      setRetrying(false);
    }
  }, []);

  useEffect(() => {
    let active = true;

    const refreshSourceStatus = () => {
      fetchHealth()
        .then((health) => {
          if (active) setSourceStatus(health);
        })
        .catch(() => {
          // Page-level health indicators already handle a fully unreachable API.
        });
    };

    refreshSourceStatus();
    const id = window.setInterval(refreshSourceStatus, 3000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!sourceStatus?.catalog_fallback_active) return undefined;
    const id = window.setTimeout(retryLiveSource, 15000);
    return () => window.clearTimeout(id);
  }, [
    retryLiveSource,
    sourceStatus?.catalog_fallback_active,
    sourceStatus?.catalog_source_checked_at,
  ]);

  return (
    <>
      <nav className="site-nav" aria-label="Demo pages">
        <div className="site-nav-links">
          {PAGES.map((page) => (
            <a
              key={page.href}
              href={page.href}
              className="nav-link"
              aria-current={page.href === current ? "page" : undefined}
              data-active={page.href === current}
            >
              {page.label}
            </a>
          ))}
        </div>
        {children ? <div className="site-nav-extra">{children}</div> : null}
      </nav>
      {sourceStatus?.catalog_fallback_active && (
        <div className="source-fallback-banner" role="status" aria-live="polite">
          <span className="source-fallback-icon" aria-hidden="true">!</span>
          <div className="source-fallback-copy">
            <strong>Live Merchant Unavailable</strong>
            <span>Using temporary mock catalog. Live data will be restored automatically when the merchant connection recovers.</span>
            {retryMessage && <span className="source-retry-message">{retryMessage}</span>}
          </div>
          <button
            type="button"
            className="source-retry-button"
            onClick={retryLiveSource}
            disabled={retrying}
          >
            {retrying ? "Retrying…" : "Retry live connection"}
          </button>
        </div>
      )}
    </>
  );
}
