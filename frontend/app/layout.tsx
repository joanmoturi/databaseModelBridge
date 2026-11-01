import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ModelBridge",
  description: "Application data migration control plane, development in progress",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
