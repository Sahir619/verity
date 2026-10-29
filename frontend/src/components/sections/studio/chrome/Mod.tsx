import type { ReactNode } from "react";

function Screw() {
  return (
    <svg viewBox="0 0 12 12" aria-hidden focusable="false">
      <circle cx="6" cy="6" r="4.9" fill="none" stroke="currentColor" strokeWidth="1.2" />
      <path d="M3.2 8.8 8.8 3.2" stroke="currentColor" strokeWidth="1.5" fill="none" />
    </svg>
  );
}

function Ear({ side }: { side: "l" | "r" }) {
  return (
    <i className={`ear ${side}`} aria-hidden>
      <Screw />
      <Screw />
    </i>
  );
}

/** One rack module: ears with screw holes, a "MOD · NAME" label on a 1px rule, then the body. */
export function Mod({
  label,
  right,
  className,
  ariaLabel,
  children,
}: {
  label: string;
  right?: ReactNode;
  className?: string;
  ariaLabel?: string;
  children: ReactNode;
}) {
  return (
    <section className={`mod${className ? ` ${className}` : ""}`} aria-label={ariaLabel ?? label}>
      <div className="mh">
        <span className="eng">MOD · {label.toUpperCase()}</span>
        {right ? <span className="mh-r">{right}</span> : null}
      </div>
      {children}
      <Ear side="l" />
      <Ear side="r" />
    </section>
  );
}
