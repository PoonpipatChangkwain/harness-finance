import { Suspense } from "react";

import { ResearchView } from "./ResearchView";

export default async function ResearchPage({ params }: { params: Promise<{ id?: string[] }> }) {
  const { id } = await params;
  const sessionId = id?.[0] ?? null;
  // no `key`: the layout-router remounts this segment per id (see trade/page.tsx)
  return (
    <Suspense>
      <ResearchView sessionId={sessionId} />
    </Suspense>
  );
}
