import type { LiveSlice } from "./slices/live";
import type { PlaybackSlice } from "./slices/playback";
import type { RunSlice } from "./slices/run";
import type { UiSlice } from "./slices/ui";

/** The whole module-level store: one object, four slices (ARCHITECTURE 7). */
export type Store = RunSlice & PlaybackSlice & UiSlice & LiveSlice;
