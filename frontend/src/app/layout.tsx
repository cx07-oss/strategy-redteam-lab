import type { Metadata } from "next";
import "./styles.css";
import "./product.css";

export const metadata: Metadata = {
  title: "Strategy Red Team — Deterministic Research",
  description:
    "AI-assisted adversarial portfolio research verified by a deterministic Python engine.",
};

export const viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
