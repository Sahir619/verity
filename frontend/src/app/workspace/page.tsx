import type { Metadata } from "next";
import { WorkspaceView } from "@/components/sections/workspace/WorkspaceView";

export const metadata: Metadata = { title: "Your workspace" };

export default function WorkspacePage() {
  return <WorkspaceView />;
}
