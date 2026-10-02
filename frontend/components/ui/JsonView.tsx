"use client";

import { JsonView as LiteJsonView, allExpanded, collapseAllNested, darkStyles } from "react-json-view-lite";
import "react-json-view-lite/dist/index.css";

/** JSON viewer for tool args / scratchpad entries; small objects open fully, large ones one level. */
export function JsonView({ data, expanded }: { data: unknown; expanded?: boolean }) {
  if (data === null || typeof data !== "object") {
    return <pre className="num whitespace-pre-wrap break-all text-xs text-muted">{JSON.stringify(data, null, 2)}</pre>;
  }
  const small = JSON.stringify(data).length < 600;
  return (
    <div className="num overflow-x-auto text-xs [&_*]:!bg-transparent">
      <LiteJsonView
        data={data as object}
        shouldExpandNode={expanded || small ? allExpanded : collapseAllNested}
        style={darkStyles}
      />
    </div>
  );
}
