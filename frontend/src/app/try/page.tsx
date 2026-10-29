import type { Metadata } from "next";
import { TryRedirect } from "./TryRedirect";

export const metadata: Metadata = { title: "Live bench" };

/** /try moved to /bench. A static export has no server redirects, so the page forwards on the client. */
export default function TryPage() {
  return <TryRedirect />;
}
