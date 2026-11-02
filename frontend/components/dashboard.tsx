"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  authenticatedJson, parseAudit, parseReadiness, parseWorkspaces,
  type AuditEvent, type Readiness, type Workspace,
} from "../lib/api";

export function ServiceStatus({ readiness }: { readiness: Readiness }) {
  return <div className="service-grid">
    <article className="service"><span className={readiness.checks.metadata_database ? "dot ok" : "dot"} />
      <div><h3>Metadata database</h3><p>{readiness.checks.metadata_database ? "Connected · schema current" : "Unavailable"}</p></div>
    </article>
    <article className="service"><span className={readiness.checks.worker ? "dot ok" : "dot"} />
      <div><h3>Worker heartbeat</h3><p>{readiness.checks.worker ? "Fresh · available" : "Missing or stale"}</p></div>
    </article>
  </div>;
}

export default function Dashboard() {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [serviceError, setServiceError] = useState("");
  const [credential, setCredential] = useState("");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [accessError, setAccessError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function refresh() {
      try {
        const response = await fetch("/bridge/ready", { signal: controller.signal, cache: "no-store" });
        const status = parseReadiness(await response.json());
        setReadiness(status);
        setServiceError("");
      } catch {
        if (!controller.signal.aborted) {
          setReadiness(null);
          setServiceError("API unreachable. Start the documented stack.");
        }
      }
    }
    void refresh();
    const timer = setInterval(() => { void refresh(); }, 10000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);

  async function connect(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setAccessError("");
    setWorkspaces([]);
    setAudit([]);
    try {
      const [workspaceData, auditData] = await Promise.all([
        authenticatedJson("api/v1/workspaces", credential),
        authenticatedJson("api/v1/audit", credential),
      ]);
      setWorkspaces(parseWorkspaces(workspaceData));
      setAudit(parseAudit(auditData));
      setCredential("");
    } catch (error: unknown) {
      setAccessError(error instanceof Error ? error.message : "Unable to load workspace");
    } finally {
      setLoading(false);
    }
  }

  return <div className="shell">
    <aside className="sidebar">
      <Link className="brand" href="/"><span className="brand-mark">M</span>ModelBridge</Link>
      <p className="sidebar-label">CONTROL PLANE</p>
      <nav aria-label="Main navigation"><a className="active" href="#overview">Overview</a>
        <a href="#workspace">Workspace access</a><a href="#history">Audit history</a></nav>
      <div className="sidebar-footer"><span className="development">Development</span>
        <p>PostgreSQL migration engine.<br />Migration controls use the API.</p></div>
    </aside>
    <main id="overview">
      <header><span className="eyebrow">APPLICATION DATA · CONTROL PLANE</span>
        <span className="environment">Local development</span></header>
      <section className="hero"><h1>Your data journey,<br /><span>with evidence at every step.</span></h1>
        <p>Inspect service state and workspace history. Use the documented API for approved migrations and reconciliation.</p></section>
      <section className="panel" aria-labelledby="services-heading">
        <div className="panel-heading"><h2 id="services-heading">Service health</h2>
          <span className="tag">{readiness?.status === "ready" ? "Ready" : "Not ready"}</span></div>
        {serviceError ? <p role="alert" className="error">{serviceError}</p> :
          readiness ? <ServiceStatus readiness={readiness} /> : <p role="status">Checking API readiness…</p>}
        <p className="note">Readiness is not release acceptance. Workers execute durable jobs; this dashboard displays availability, not migration progress.</p>
      </section>
      <section id="workspace" className="panel">
        <h2>Open your workspace</h2><p className="muted">Run <code>modelbridge bootstrap --name &quot;My workspace&quot;</code> once, then use its owner credential.</p>
        <form onSubmit={connect}>
          <label htmlFor="credential">Owner access credential</label>
          <div className="access-row"><input id="credential" type="password" autoComplete="off"
            value={credential} onChange={(event) => setCredential(event.target.value)} required
            aria-describedby="credential-note" /><button disabled={loading}>{loading ? "Loading…" : "Load workspace"}</button></div>
          <p id="credential-note" className="note">Held in memory only. Never saved in browser storage.</p>
        </form>
        {accessError && <p role="alert" className="error">{accessError}</p>}
        {workspaces.map((workspace) => <article className="workspace" key={workspace.id}>
          <h3>{workspace.name}</h3><code>{workspace.id}</code>
          <p className="muted">Created {new Date(workspace.created_at).toLocaleString()}</p>
        </article>)}
      </section>
      <section id="history" className="panel"><h2>Audit history</h2>
        {audit.length === 0 ? <p className="muted">Load a workspace to view its real audit events.</p> :
          <table><thead><tr><th>Event</th><th>Time</th><th>Object</th></tr></thead>
            <tbody>{audit.map((event) => <tr key={event.id}><td>{event.action}</td>
              <td>{new Date(event.created_at).toLocaleString()}</td><td><code>{event.object_id}</code></td></tr>)}</tbody></table>}
      </section>
      <footer>ModelBridge · Development foundation · No invented migration metrics</footer>
    </main>
  </div>;
}
