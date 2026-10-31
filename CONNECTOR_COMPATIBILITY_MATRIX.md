# Connector compatibility matrix

Date: October 31, 2026.
The supplied repository has no product source or connector declarations.
No database product or version has been tested or certified.

| Connector | Database products | Tested versions | Source/destination | Schema features | Data types | Known limitations | Integration coverage | Maturity |
|---|---|---|---|---|---|---|---|---|
| None supplied | Unknown | None | Unknown | Unknown | Unknown | Implementation unavailable | None executed | Unclassified; not certified |

There are no advertised stable connectors available to verify. This does not mean
connector acceptance passed vacuously: the real migration criterion remains unmet.
No connector can be labeled Experimental, Beta, or Stable without identifying its
implementation and evidence; unclassified is not production support.

## Certification evidence required per implemented connector

Record exact server version, connector/client version, independent source and
destination instances, supported roles, discovery and schema features, supported
types and conversions, limitations, executed integration cases, and maturity.
Include foreign keys, transactions, nullability, date handling, identifier changes,
duplicate/conflicting records, failure recovery, and non-destructive behavior.
Stable claims must be limited to explicitly exercised deployment/version profiles.

## Pending student management exercise

No source/destination databases or fixtures were created because ModelBridge
cannot connect to or migrate them. The required future fixture must contain
students, teachers, courses, enrollments, and payments with foreign keys, nullable
and required values, mixed date encodings, renamed fields, differing identifiers,
and missing/inconsistent records.

Exercise connection tests, discovery, comparison, recommendations, approval,
transformations, validation, dry run, sample, full migration, and reconciliation.
Independently query destination row counts, transformed values, foreign-key
integrity, payment totals, and rejected/conflicting records. Verify dry runs do not
write and reruns do not silently overwrite or discard records.
