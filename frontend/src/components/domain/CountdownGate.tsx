import { Check, CirclePause, Timer } from "lucide-react";

export type CountdownGateProps = {
  state: "counting" | "held" | "waiting" | "closed";
  remainingMs: number;
  totalMs: number;
  onBuildNow?: () => void;
  onHold?: () => void;
  onResume?: () => void;
};

/**
 * Plan approval gate, drawn in the studio's datasheet style (it renders inside Figure 1's PLAN box, live runs only).
 * The countdown is adjustable: hold it or build now. With Autopilot off it waits for a click.
 */
export function CountdownGate({ state, remainingMs, totalMs, onBuildNow, onHold, onResume }: CountdownGateProps) {
  const seconds = Math.max(0, Math.ceil(remainingMs / 1000));
  const ratio = totalMs > 0 ? Math.max(0, Math.min(1, remainingMs / totalMs)) : 0;

  let title: string;
  let icon = <Timer aria-hidden className="ico" />;
  switch (state) {
    case "counting":
      title = `Building in ${seconds} s`;
      break;
    case "held":
      title = `Held at ${seconds} s`;
      icon = <CirclePause aria-hidden className="ico" />;
      break;
    case "waiting":
      title = "Waiting for you.";
      break;
    case "closed":
      title = "Plan approved. Building.";
      icon = <Check aria-hidden className="ico" />;
      break;
  }

  return (
    <div className="gate">
      <p role="timer" aria-live="off" className="gate-t">
        {icon}
        {title}
      </p>
      {state === "counting" || state === "held" ? (
        <div
          className="gate-bar"
          role="progressbar"
          aria-label="Time until the build starts"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(ratio * 100)}
        >
          <span style={{ transform: `scaleX(${ratio})` }} />
        </div>
      ) : null}
      {state === "closed" ? null : (
        <div className="gate-acts">
          {state === "counting" ? (
            <>
              <button type="button" className="btn sm" onClick={onBuildNow}>
                Build now
              </button>
              <button type="button" className="btn sm ghost" onClick={onHold}>
                Hold
              </button>
            </>
          ) : null}
          {state === "held" ? (
            <>
              <button type="button" className="btn sm" onClick={onResume}>
                Resume
              </button>
              <button type="button" className="btn sm ghost" onClick={onBuildNow}>
                Build now
              </button>
            </>
          ) : null}
          {state === "waiting" ? (
            <button type="button" className="btn sm" onClick={onBuildNow}>
              Build
            </button>
          ) : null}
        </div>
      )}
      {state === "counting" ? <p className="gate-note">Pin a block or toggle a variant to steer. Every change restarts the countdown.</p> : null}
    </div>
  );
}
