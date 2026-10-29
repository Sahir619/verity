import { Suspense } from "react";
import type { Metadata } from "next";
import { BenchView } from "@/components/sections/bench/BenchView";

export const metadata: Metadata = { title: "Live bench" };

export default function BenchPage() {
  return (
    <Suspense fallback={null}>
      <BenchView />
    </Suspense>
  );
}
