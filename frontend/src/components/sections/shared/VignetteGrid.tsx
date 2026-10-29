"use client";

import { useEffect, useRef } from "react";

/** Fraction of the distance to the pointer covered per frame. */
const EASE = 0.1;

/**
 * Fixed, click-through grid behind every page (the first child of each datasheet root). The vignette centre follows the pointer: pointermove only stores a target,
 * and a requestAnimationFrame loop lerps the centre toward it and writes --mx/--my on the element (no React state).
 * Touch, coarse pointers and reduced motion keep the static vignette in the upper middle (the CSS defaults).
 */
export function VignetteGrid() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fine = window.matchMedia("(hover: hover) and (pointer: fine)");
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (!fine.matches || calm.matches) return;

    let tx = window.innerWidth * 0.5;
    let ty = window.innerHeight * 0.28;
    let x = tx;
    let y = ty;
    let raf = 0;

    const tick = () => {
      x += (tx - x) * EASE;
      y += (ty - y) * EASE;
      el.style.setProperty("--mx", `${x.toFixed(1)}px`);
      el.style.setProperty("--my", `${y.toFixed(1)}px`);
      raf = Math.abs(tx - x) < 0.3 && Math.abs(ty - y) < 0.3 ? 0 : requestAnimationFrame(tick);
    };

    const onMove = (e: PointerEvent) => {
      if (e.pointerType !== "mouse" && e.pointerType !== "pen") return;
      tx = e.clientX;
      ty = e.clientY;
      if (!raf) raf = requestAnimationFrame(tick);
    };

    // Start from the static position so the first move eases in instead of jumping.
    el.style.setProperty("--mx", `${x}px`);
    el.style.setProperty("--my", `${y}px`);
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      window.removeEventListener("pointermove", onMove);
      cancelAnimationFrame(raf);
    };
  }, []);

  return <div ref={ref} className="ds-vg" aria-hidden="true" />;
}
