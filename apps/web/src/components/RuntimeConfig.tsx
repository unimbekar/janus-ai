"use client";

import { configureApi } from "@/lib/api";

/**
 * Hands the server-resolved API origin to the browser bundle.
 *
 * Next inlines `NEXT_PUBLIC_*` variables during `next build`, which would freeze
 * the API origin into the image. Passing it down from a server component instead
 * keeps the same artifact deployable in every environment.
 *
 * When unset the client stays on this origin and the web server proxies through.
 */
export function RuntimeConfig({ apiUrl }: { apiUrl?: string }) {
  configureApi(apiUrl);
  return null;
}
