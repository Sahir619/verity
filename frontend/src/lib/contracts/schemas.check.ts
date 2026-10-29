// Compile-time check that the zod schemas and the hand-written types in ./types.ts describe the same shapes.
// Type-only: nothing here runs, and nothing imports this file.
import type { z } from "zod";
import type { CanvasOp, Candidate, RunEvent, RunLog } from "./types";
import type { CanvasOpSchema, CandidateSchema, RunEventSchema, RunLogSchema } from "./schemas";

type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;

export const _canvasOp: Same<z.infer<typeof CanvasOpSchema>, CanvasOp> = true;
export const _candidate: Same<z.infer<typeof CandidateSchema>, Candidate> = true;
export const _runEvent: Same<z.infer<typeof RunEventSchema>, RunEvent> = true;
export const _runLog: Same<z.infer<typeof RunLogSchema>, RunLog> = true;
