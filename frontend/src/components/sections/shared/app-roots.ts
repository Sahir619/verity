import { RACK_FACES } from "./rack-fonts";
import "./datasheet-base.css";
import "./app-pages.css";

/** Root class of the /try forwarding page: the rack scope (.vt) plus the rack font variables. */
export const TRY_ROOT = `vt ${RACK_FACES}`;

/** Root class of /results: the rack scope (.vr) plus the rack font variables. */
export const RESULTS_ROOT = `vr ${RACK_FACES}`;

/** Root class of the 404 page: the rack scope (.vn) plus the rack font variables. */
export const NOT_FOUND_ROOT = `vn ${RACK_FACES}`;
