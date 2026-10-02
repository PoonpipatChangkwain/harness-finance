import { Suspense } from "react";

import { PortfolioView } from "./PortfolioView";

export default function PortfolioPage() {
  return (
    <Suspense>
      <PortfolioView />
    </Suspense>
  );
}
