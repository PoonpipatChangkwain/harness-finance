"use client";

/**
 * Final answer markdown (ui-spec §4.1): `[n]` markers become CitationChips and
 * numbers listed in `unverified_numbers` get an amber wavy underline.
 * Markers are turned into `#cite-n` / `#unverified` links before rendering so
 * they survive inside tables and lists; the `a` renderer swaps them back.
 */

import { TriangleAlert } from "lucide-react";
import type { ComponentProps } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Citation } from "@/lib/events";
import { useT } from "@/lib/i18n";

import { CitationChip } from "./CitationChip";

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function markAnswer(markdown: string, citations: Citation[], unverified: string[]): string {
  let out = markdown;
  for (const num of [...unverified].sort((a, b) => b.length - a.length)) {
    // a whole number token, not inside another number or a [n] marker
    const re = new RegExp(`(?<![\\d.,\\[])${escapeRe(num)}(?![\\d\\]]|\\.\\d|,\\d)`, "g");
    out = out.replace(re, `[${num}](#unverified)`);
  }
  const markers = new Set(citations.map((c) => c.marker));
  return out.replace(/\[(\d+)\](?!\()/g, (m, n: string) => (markers.has(Number(n)) ? `[${n}](#cite-${n})` : m));
}

/** Streaming draft: raw `[^prov_…]` markers are not resolved yet, so hide them. */
export function stripRawMarkers(text: string): string {
  return text.replace(/\s?\[\^prov_[A-Za-z0-9]+\]/g, "");
}

export function Answer({
  markdown,
  citations,
  unverified,
  runId,
  streaming = false,
}: {
  markdown: string;
  citations: Citation[];
  unverified: string[];
  runId: string;
  streaming?: boolean;
}) {
  const t = useT();
  const byMarker = new Map(citations.map((c) => [c.marker, c.provenance_id]));
  const source = streaming ? stripRawMarkers(markdown) : markAnswer(markdown, citations, unverified);
  const components: ComponentProps<typeof Markdown>["components"] = {
    a({ href, children, ...rest }) {
      if (href?.startsWith("#cite-")) {
        const marker = Number(href.slice(6));
        const pid = byMarker.get(marker);
        if (pid) return <CitationChip marker={marker} provenanceId={pid} runId={runId} />;
      }
      if (href === "#unverified") {
        return (
          <span
            className="fh-unverified"
            data-testid="unverified-number"
            title={t("ตัวเลขนี้ไม่พบใน provenance ของ run นี้ (อาจไม่ได้มาจาก tool)", "This number is not in this run's provenance (it may not come from a tool)")}
          >
            {children}
          </span>
        );
      }
      return (
        <a href={href} target="_blank" rel="noreferrer" className="text-accent underline" {...rest}>
          {children}
        </a>
      );
    },
  };
  return (
    <div data-testid="answer" className="fh-markdown min-w-0 text-[14px] leading-relaxed">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>
        {source}
      </Markdown>
      {streaming ? <span className="fh-pulse ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 bg-accent" /> : null}
      {!streaming && unverified.length ? (
        <div className="mt-2 flex items-center gap-1.5 text-[12.5px] text-warning">
          <TriangleAlert className="size-3.5" aria-hidden />
          {t(
            `${unverified.length} ตัวเลขไม่พบใน provenance (hover ที่ตัวเลขที่ขีดเส้นใต้)`,
            `${unverified.length} number(s) not found in provenance (hover the underlined numbers)`,
          )}
        </div>
      ) : null}
    </div>
  );
}
