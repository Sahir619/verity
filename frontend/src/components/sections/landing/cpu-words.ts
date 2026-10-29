import { CPU } from "./cpu-real";

const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];

/** A count as a word in running text ("seven"), the digit past twelve. */
export const numberWord = (n: number) => WORDS[n] ?? String(n);

/** "a of b" for a passed and total pair. */
export const ofPair = ([p, t]: readonly [number, number]) => `${p} of ${t}`;

/** The provider as a name ("nebius" becomes "Nebius"). */
export const PROVIDER = CPU.provider.charAt(0).toUpperCase() + CPU.provider.slice(1);
