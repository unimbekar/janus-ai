/**
 * Container liveness. The web app has no dependencies of its own — if the
 * process is answering, it is serving — so this deliberately does not probe the
 * API. A control-plane outage should not restart the browser tier.
 */
export function GET() {
  return Response.json({ status: "ok", service: "janus-web" });
}
