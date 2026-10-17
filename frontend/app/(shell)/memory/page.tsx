import { Suspense } from "react";

import { MemoryView } from "@/components/memory/MemoryView";

export default function MemoryPage() {
  return (
    <Suspense>
      <MemoryView />
    </Suspense>
  );
}
