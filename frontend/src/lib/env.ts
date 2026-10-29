// Public configuration only. Never put keys or tokens here (NEXT_PUBLIC_* is bundled into client JavaScript).
export const AGENT_URL: string | null = process.env.NEXT_PUBLIC_AGENT_URL || null;
export const isLiveConfigured: boolean = AGENT_URL !== null;

// Accounts (Supabase sign-in). The project URL and the publishable key are public by design; the secret key lives only on the server.
// With neither set there is no sign-in gate and the bench works as it does in local development.
export const SUPABASE_URL: string | null = process.env.NEXT_PUBLIC_SUPABASE_URL || null;
export const SUPABASE_PUBLISHABLE_KEY: string | null = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || null;
export const isAccountsConfigured: boolean = SUPABASE_URL !== null && SUPABASE_PUBLISHABLE_KEY !== null;

/** Where the landing's live button goes: the sign-in page when accounts are set up (it forwards to the bench), else the bench. */
export const LIVE_HREF: string = isAccountsConfigured ? "/signin?next=/bench" : "/bench";
