"use client";

/**
 * Scroll-follow (after dsh ui-chat `use-scroll-follow.ts`): stick to the
 * bottom while the user is at the bottom, stop following when they scroll up,
 * expose `atBottom` + `scrollToBottom` for the "↓ ล่าสุด" button.
 */

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

const THRESHOLD_PX = 48;

export function useScrollFollow(container: RefObject<HTMLElement | null>) {
  const following = useRef(true);
  const [atBottom, setAtBottom] = useState(true);

  const scrollToBottom = useCallback(
    (smooth = false) => {
      const el = container.current;
      if (!el) return;
      following.current = true;
      setAtBottom(true);
      el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
    },
    [container],
  );

  useEffect(() => {
    const el = container.current;
    if (!el) return;
    const onScroll = () => {
      const bottom = el.scrollHeight - el.scrollTop - el.clientHeight <= THRESHOLD_PX;
      following.current = bottom;
      setAtBottom(bottom);
    };
    const observer = new MutationObserver(() => {
      if (following.current) el.scrollTop = el.scrollHeight;
    });
    observer.observe(el, { childList: true, subtree: true, characterData: true });
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      observer.disconnect();
      el.removeEventListener("scroll", onScroll);
    };
  }, [container]);

  return { atBottom, scrollToBottom };
}
