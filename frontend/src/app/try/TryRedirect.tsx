"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { TRY_ROOT } from "@/components/sections/shared/app-roots";

export function TryRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/bench");
  }, [router]);
  return (
    <main className={`${TRY_ROOT} flex min-h-svh items-center justify-center p-6`}>
      <p className="text-base text-fg-muted">
        Live runs moved to the{" "}
        <Link prefetch={false} href="/bench" className="text-foreground underline underline-offset-4">
          live bench
        </Link>
        .
      </p>
    </main>
  );
}
