import type { Metadata } from "next";
import { TooltipProvider } from "@/components/ui/tooltip";
import { RACK_FACES } from "@/components/sections/shared/rack-fonts";
import "./globals.css";

const description =
  "Describe a chip in plain English. Verity writes the Verilog, tests it, fixes its own bugs, then redesigns it to run faster and smaller. Built on NVIDIA Nemotron and Nebius.";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"),
  title: { template: "%s | Verity", default: "Verity: AI Hardware Engineer" },
  description,
  openGraph: {
    title: "Verity: AI Hardware Engineer",
    description,
    images: ["/og.png"],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${RACK_FACES}`}>
      <body>
        <TooltipProvider>{children}</TooltipProvider>
      </body>
    </html>
  );
}
