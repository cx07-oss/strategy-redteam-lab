import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Trading Strategy Red-Team Lab",
  description:
    "Read-only replay of verified, deterministic trading-strategy stress-test telemetry.",
};

export const viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
