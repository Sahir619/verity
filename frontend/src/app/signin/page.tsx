import { Suspense } from "react";
import type { Metadata } from "next";
import { SignInView } from "@/components/sections/signin/SignInView";

export const metadata: Metadata = { title: "Sign in" };

export default function SignInPage() {
  return (
    <Suspense fallback={null}>
      <SignInView />
    </Suspense>
  );
}
