import { Martian_Mono, Sofia_Sans, Sofia_Sans_Extra_Condensed } from "next/font/google";

// The rack faces: Sofia Sans Extra Condensed for display, Sofia Sans for body, Martian Mono for data
// and labels. Every page root adds RACK_FACES so the variables resolve inside it.
export const sofiaCondensed = Sofia_Sans_Extra_Condensed({
  subsets: ["latin"],
  weight: ["800", "900"],
  variable: "--font-rk-display",
  display: "swap",
});

export const sofiaSans = Sofia_Sans({
  subsets: ["latin"],
  variable: "--font-rk-body",
  display: "swap",
});

export const martianMono = Martian_Mono({
  subsets: ["latin"],
  variable: "--font-rk-mono",
  display: "swap",
});

export const RACK_FACES = `${sofiaCondensed.variable} ${sofiaSans.variable} ${martianMono.variable}`;
