import { ArrowRight } from "lucide-react";

/**
 * Joining blocks into a bigger unit is not built. This is a picture of the idea, marked as such: no number, no working
 * control. The button is disabled and says why. Verity builds one block at a time today.
 */
export function CombinePreview() {
  return (
    <section className="w-combine" aria-labelledby="w-combine-h">
      <div className="w-combine-txt">
        <div className="w-combine-top">
          <h2 className="w-h2" id="w-combine-h">
            Combine blocks
          </h2>
          <span className="badge">Coming soon</span>
        </div>
        <p className="w-note-p">Verity builds one block at a time today.</p>
      </div>
      <div
        className="w-flow"
        role="img"
        aria-label="Preview only: a MAC array, then a requantize block, then a FIFO would make an AI accelerator tile."
      >
        <span className="w-blk">MAC array</span>
        <ArrowRight aria-hidden />
        <span className="w-blk">Requantize</span>
        <ArrowRight aria-hidden />
        <span className="w-blk">FIFO</span>
        <span className="w-makes">makes</span>
        <span className="w-blk w-blk-big">AI accelerator tile</span>
      </div>
      <button type="button" className="bx" disabled aria-disabled="true">
        Combine (not available yet)
      </button>
    </section>
  );
}
