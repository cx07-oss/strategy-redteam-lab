import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = { title: "Verified Run Replay", description: "Read-only research replay of verified strategy red-team telemetry." };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
