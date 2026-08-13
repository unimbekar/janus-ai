import type { Metadata } from "next";
import { RuntimeConfig } from "@/components/RuntimeConfig";
import "./globals.css";

export const metadata: Metadata = {
  title: "Janus Intelligence",
  description: "One interface over every model — cloud, private, or your own.",
  icons: { icon: "/favicon.svg" },
};

// The API origin is read per request, so this shell cannot be prerendered.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* Unset in normal deployments, where the API is proxied on this origin. */}
        <RuntimeConfig apiUrl={process.env.JANUS_PUBLIC_API_URL} />
        {children}
      </body>
    </html>
  );
}
