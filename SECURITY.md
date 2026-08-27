# Security policy

ReconfigNet-Sim is a research testbed, not a production control service. Do
not use the example configurations or sample credentials as a security
boundary for a live OCS or switch.

## Reporting a vulnerability

Please do not disclose credentials, private topology information or an
unfixed vulnerability in a public issue. Use GitHub's private vulnerability
reporting or Security Advisory flow for this repository. Include the affected
revision, target/profile, reproduction steps and the smallest safe artifact
that demonstrates the problem.

If a report contains a secret, revoke or rotate it first and mention only the
secret type and affected service in the report. Do not attach the secret.

## Scope

Reports involving the Agent APIs, lease/revision handling, backend ownership,
configuration validation, CI permissions or accidental disclosure of private
deployment data are in scope. Physical OCS safety, vendor firmware and
site-specific BF-SDE issues must also be reported to the appropriate hardware
owner.

The maintainers will acknowledge reports through the private channel and will
coordinate disclosure after a fix or mitigation is available.
