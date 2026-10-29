import { RACK_FACES } from "../shared/rack-fonts";
import { VignetteGrid } from "../shared/VignetteGrid";
import { CpuToday } from "../landing/CpuToday";
import { DemoVideo } from "../landing/DemoVideo";
import { Hero } from "../landing/Hero";
import { Ladder } from "../landing/Ladder";
import { LoopSection } from "../landing/LoopSection";
import { Problem } from "../landing/Problem";
import { Inside } from "./Inside";
import { RackFooter } from "./RackFooter";
import { RackNav } from "./RackNav";
import "../shared/datasheet-base.css";
import "./rack.css";
import "../landing/landing.css";

/** The landing page in the rack world (amber accent), scoped under .rk. Every figure comes from a run file, except the
 *  three sourced facts in Problem. */
export function RackPage() {
  return (
    <div className={`rk land ${RACK_FACES}`} id="top">
      <VignetteGrid />
      <RackNav />
      <main>
        <Hero />
        <Problem />
        <LoopSection />
        <Inside />
        <CpuToday />
        <Ladder />
        <DemoVideo />
      </main>
      <RackFooter />
    </div>
  );
}
