import { Suspense } from "react";

import { BacktestView } from "./BacktestView";

export default async function BacktestPage({ params }: { params: Promise<{ id?: string[] }> }) {
  const { id } = await params;
  return (
    <Suspense>
      <div className="mx-auto w-full max-w-[1400px] p-4">
        <BacktestView id={id?.[0] ?? null} />
      </div>
    </Suspense>
  );
}
