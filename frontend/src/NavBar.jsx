const PAGES = [
  { href: "/demo", label: "Judge Mode" },
  { href: "/growth", label: "Growth Results" },
  { href: "/", label: "Checkout" },
  { href: "/merchant", label: "Merchant" },
  { href: "/a2a", label: "A2A" },
  { href: "/guardrails", label: "Guardrails" },
];

export default function NavBar({ current, children }) {
  return (
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
  );
}
