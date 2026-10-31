# Release candidate checklist

Date: October 31, 2026.
Current gate: **NOT READY**; no product commit is available.

- [x] Clone the supplied repository independently.
- [x] Confirm repository identity and absence of commits/refs.
- [x] Record missing prerequisites without inventing passing results.
- [ ] Obtain intended populated source and immutable release commit.
- [ ] Establish documented supported OS/deployment profile and system requirements.
- [ ] Provision a genuinely isolated VM/container environment.
- [ ] Install solely from documented instructions and locked dependencies.
- [ ] Start API, dashboard, worker, metadata database, and migrations.
- [ ] Verify health, readiness, restart, and state persistence.
- [ ] Configure independent source/destination databases and student fixtures.
- [ ] Test connections, discover schemas, compare, and recommend mappings.
- [ ] Review/approve mappings, configure transforms, and validate.
- [ ] Run dry, sample, and complete migrations.
- [ ] Independently reconcile destination values, relationships, and rejects.
- [ ] Verify no silent overwrites/discards and no dry-run mutations.
- [ ] Assess encryption, auth/authz, tenant isolation, SSRF, and transport security.
- [ ] Verify redaction, audit logs, permissions, and dangerous configuration handling.
- [ ] Exercise failure recovery, metadata backup/restore, and upgrade.
- [ ] Certify every advertised stable connector against stated versions.
- [ ] Finish first-time user documentation and repeat unfamiliar-user test.
- [ ] Verify Compose, environment reference, CLI, OpenAPI, and dashboard.
- [ ] Verify license and contribution process.
- [ ] Build versioned Docker images and Python packages reproducibly.
- [ ] Prepare publication instructions against actual package/image identifiers.
- [ ] Resolve all Critical/High findings and retest affected functionality.
- [ ] Reassess verdict against the explicitly tested deployment profile.

## Publication hold

No packages/images were built or published. No registry instructions were invented
without Dockerfiles, manifests, package names, or a release version. Once supplied,
document registry-specific authentication, local build, reproducibility checks,
artifact inspection, checksums/digests, and the separately authorized publication
step. Never embed credentials in examples.

No commit, push, public release, repository visibility change, or publication is
authorized by this checklist. Source presence alone will not satisfy acceptance.
