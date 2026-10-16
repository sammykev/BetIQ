"use client";

import { useEffect, useState } from "react";

/**
 * True while the reader is scrolling down the page (past `offset` px, by
 * more than `threshold` px at a time); false when they scroll up or are
 * near the top. Drives the compact menu bar and the hiding header.
 */
export function useScrollingDown(offset = 80, threshold = 8) {
  const [down, setDown] = useState(false);

  useEffect(() => {
    let last = window.scrollY;
    let frame = 0;
    const read = () => {
      frame = 0;
      const y = window.scrollY;
      if (y < offset) setDown(false);
      else if (Math.abs(y - last) > threshold) setDown(y > last);
      else return;
      last = y;
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(read); };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [offset, threshold]);

  return down;
}
