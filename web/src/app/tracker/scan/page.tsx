import type { Metadata } from "next";
import { AccountScan } from "@/components/tracker/scan/AccountScan";

export const metadata: Metadata = { title: "Privacy scan | am-i.exposed" };

export default function Page() {
  return <AccountScan />;
}
