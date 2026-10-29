import { Suspense } from "react";
import type { Metadata } from "next";
import { ResultsSkeleton } from "@/components/sections/results/ResultsSkeleton";
import { ResultsView } from "@/components/sections/results/ResultsView";

export const metadata: Metadata = { title: "Results: 4-lane MAC array, 8-bit" };

export default function ResultsPage() {
  return (
    <Suspense fallback={<ResultsSkeleton />}>
      <ResultsView />
    </Suspense>
  );
}
