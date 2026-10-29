// Public, non-secret URLs. The environment can override each one at build time; a link whose URL is unset (Devpost
// until the entry is published) is not rendered, so no link ever goes nowhere.
export const GITHUB_URL: string | null = process.env.NEXT_PUBLIC_GITHUB_URL || "https://github.com/Sahir619/verity";
export const DEVPOST_URL: string | null = process.env.NEXT_PUBLIC_DEVPOST_URL || null;
export const DEMO_VIDEO_URL: string | null = process.env.NEXT_PUBLIC_DEMO_VIDEO_URL || "https://youtu.be/hUz8faULujQ";
