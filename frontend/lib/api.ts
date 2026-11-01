export interface Readiness {
  status: "ready" | "not_ready";
  checks: { metadata_database: boolean; worker: boolean };
}

export interface Workspace {
  id: string;
  name: string;
  created_at: string;
}

export interface AuditEvent {
  id: string;
  action: string;
  object_id: string;
  created_at: string;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function parseReadiness(value: unknown): Readiness {
  if (!isObject(value) || !isObject(value.checks) ||
      (value.status !== "ready" && value.status !== "not_ready") ||
      typeof value.checks.metadata_database !== "boolean" ||
      typeof value.checks.worker !== "boolean") {
    throw new Error("API returned an invalid readiness response");
  }
  return {
    status: value.status,
    checks: { metadata_database: value.checks.metadata_database, worker: value.checks.worker },
  };
}

export function parseWorkspaces(value: unknown): Workspace[] {
  if (!Array.isArray(value)) throw new Error("Invalid workspace response");
  return value.map((item: unknown) => {
    if (!isObject(item) || typeof item.id !== "string" ||
        typeof item.name !== "string" || typeof item.created_at !== "string") {
      throw new Error("Invalid workspace response");
    }
    return { id: item.id, name: item.name, created_at: item.created_at };
  });
}

export function parseAudit(value: unknown): AuditEvent[] {
  if (!Array.isArray(value)) throw new Error("Invalid audit response");
  return value.map((item: unknown) => {
    if (!isObject(item) || typeof item.id !== "string" || typeof item.action !== "string" ||
        typeof item.object_id !== "string" || typeof item.created_at !== "string") {
      throw new Error("Invalid audit response");
    }
    return { id: item.id, action: item.action, object_id: item.object_id, created_at: item.created_at };
  });
}

export async function authenticatedJson(path: string, token: string): Promise<unknown> {
  const response = await fetch(`/bridge/${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("Invalid owner credential. Check it and try again.");
    throw new Error("Workspace service is unavailable. Check API readiness.");
  }
  return response.json();
}
