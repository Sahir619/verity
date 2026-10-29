import type { ReactNode } from "react";
import { RackBar, type RackPage } from "./RackBar";

export type AppHeaderProps = { context?: ReactNode; actions?: ReactNode; current?: RackPage };

/** Top bar of /try, /results and the 404: the shared rack bar with a context slot and an actions slot. */
export function AppHeader({ context, actions, current }: AppHeaderProps) {
  return <RackBar current={current} context={context} actions={actions} />;
}
