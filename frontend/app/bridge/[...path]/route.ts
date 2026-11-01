import { NextRequest, NextResponse } from "next/server";

interface RouteContext {
  params: Promise<{ path: string[] }>;
}

export async function GET(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  const joined = path.join("/");
  const allowed = ["health", "ready", "version", "api/v1/workspaces", "api/v1/audit"];
  if (!allowed.includes(joined) || request.nextUrl.search) {
    return NextResponse.json({ error: "Route not found" }, { status: 404 });
  }
  const origin = process.env.MODELBRIDGE_API_URL ?? "http://127.0.0.1:8000";
  const headers: Record<string, string> = {};
  const authorization = request.headers.get("authorization");
  if (authorization && authorization.length <= 150) headers.Authorization = authorization;
  try {
    const response = await fetch(`${origin}/${joined}`, {
      headers,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(7000),
    });
    const payload: unknown = await response.json();
    return NextResponse.json(payload, {
      status: response.status,
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json({ error: "API unavailable" }, { status: 503 });
  }
}
