import type { Metadata } from "next";
import Link from "next/link";
import { AppHeader } from "@/components/sections/shared/AppHeader";
import { NOT_FOUND_ROOT } from "@/components/sections/shared/app-roots";
import { VignetteGrid } from "@/components/sections/shared/VignetteGrid";
import { EmptyState } from "@/components/domain/EmptyState";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = { title: "Not found" };

export default function NotFound() {
  return (
    <div className={`${NOT_FOUND_ROOT} flex min-h-svh flex-col`}>
      <VignetteGrid />
      <AppHeader />
      <main className="flex flex-1 items-center justify-center px-6 py-16">
        <EmptyState
          title="This page does not exist."
          body="The recorded run is a good place to start."
          action={
            <div className="flex flex-wrap items-center justify-center gap-4">
              <Button asChild className="h-10 px-4">
                <Link prefetch={false} href="/studio?run=mac4-real">Watch the recorded run</Link>
              </Button>
              <Button asChild variant="ghost">
                <Link prefetch={false} href="/">Go to the home page</Link>
              </Button>
            </div>
          }
        />
      </main>
    </div>
  );
}
