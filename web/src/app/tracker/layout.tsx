import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Wallets | am-i.exposed",
  description: "Your own wallets on every chain: what you hold, what ties them together and where their funds came from.",
  // A self-hosted page with private data: never indexed.
  robots: { index: false, follow: false },
};

export default function WalletsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
