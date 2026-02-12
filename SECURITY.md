# Security Policy

## Supported scope

DPM is intended for trusted network environments.

Security reports are welcome for:
- command handling and process execution pathways
- message parsing and serialization
- privilege boundaries in service installation
- denial-of-service vectors in output and telemetry paths

## Reporting a vulnerability

Please report privately by opening a GitHub security advisory for this repository.
If that is not available, open an issue with minimal sensitive detail and request private follow-up.

Please include:
- affected component(s)
- impact assessment
- reproduction steps
- suggested mitigation (if known)

## Response expectations

- Initial triage target: within 5 business days
- Status updates: as investigation progresses
- Fix release: based on severity and scope

## Hardening notes

- Run agent with least privilege.
- Restrict multicast segment to trusted hosts.
- Review systemd unit capabilities before deployment.
- Prefer explicit process command definitions and controlled configs.
