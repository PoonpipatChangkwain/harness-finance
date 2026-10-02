import { Suspense } from "react";

import { TradeView } from "./TradeView";

export default async function TradePage({ params }: { params: Promise<{ runId?: string[] }> }) {
  const { runId } = await params;
  const id = runId?.[0] ?? null;
  // no `key` here: Next's layout-router already remounts this segment per runId
  // (its cache key includes the param), and a second key on the same id
  // collides during navigation ("two children with the same key").
  return (
    <Suspense>
      <TradeView runId={id} />
    </Suspense>
  );
}
