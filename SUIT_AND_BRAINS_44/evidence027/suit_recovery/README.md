# Build 027 Suit evidence integration

The two supplied evidence PDFs are byte-identical (SHA-256 42c2965871c23da352db3574b3a83635bd339a76fa5fcacbba9d11218ddff199). This merge adds executable offline qualification and reference material under evidence027/suit_recovery. All original archive members retain their original uncompressed bytes. The upstream SHA256SUMS.txt applies to its original member set. The additional SHA256SUMS.txt here applies to the new files.

| Material | Decision | Engineering evidence |
|---|---|---|
| 3.2.3 monitor test and definition registry | Integrated as offline qualification | Recovered test source from Soso so.zip and the registry from the nested PH6 Codex corpus inside Suit.zip. Both Build 027 monitor module hashes match the prior recovered specimens. The exact vendored code passes all 39 historical checks with the recovered registry, including the four registry-dependent checks. This updates the earlier missing-evidence assessment for the assembled test inputs. |
| Nine Suit 4.0 schemas and 66-entry source manifest | Integrated as offline interface evidence | Extracted directly from the uploaded PDF attachments. Each schema matches the frozen size, SHA-256 and BLAKE2b-256. The optional live probe validates all schema definitions and checks the version boundary against actual Build 027 receipts. |
| Phase D repair candidate 0.2.0 | Included as an isolated reference | Original Python from Suit.zip passes 44 self-tests. Its SQLite journal is relevant to durable local IOUs, with a separate canonical profile and asserted roles. Its module is offline and outside Build 027's production import paths. |
| Frozen 4.0 A–E pipeline and Build 027 live runtime | Exact integration depends on recovering the frozen implementations and fixtures | Build 027's 3.2.2 control evaluates a distinct LOG_ADVISORY candidate. The 3.2.3 record reports a committed Suit decision. Those objects have distinct producer contracts, digest profiles and payloads. |

## Run from the extracted Build 027 root

- `python evidence027/suit_recovery/verify.py` verifies the schemas against the manifest and replays the historical monitor suite against the packaged vendor files using the standard library.
- `python evidence027/suit_recovery/verify.py --compare-runtime` also runs the Build 027 joint experiment in a temporary directory and uses the optional development dependency `jsonschema` to test receipt shape.
- `python evidence027/suit_recovery/phase_d_candidate/phase_d_candidate.py self-test` runs the standalone Phase D candidate in isolation.

The live experiment yielded 54 audit receipts. All retained the Build 027 authority markers and source trace binding. All 54 disagreed with the frozen 4.0 disposition schema. For the INVESTIGATION_HOLD example, the absent fields are candidate_digest, envelope_digest, governed_state_digest, normalizer_build_hash, producer, reasons, record_version and verdict. The existing record also has keys forbidden by the 4.0 schema and a different record_digest representation. Structural validation cannot supply the governed state, verdict, producer identity, or actual canonical artifact bytes needed for a valid 4.0 record.

## What is needed for the next runtime change

For durable IOUs, begin with a separate local replay test driven by Build 027's server-created IOU intents. Bind owner identity to the authenticated dispatch principal. Define explicit request-kind semantics and stable identity. Test process restart, duplicate delivery and partial acknowledgement, principal isolation, and recovery from a crash between the Suit commit and local IOU persistence. Choose either an atomic shared transaction or a committed outbox plus idempotent consumer. Keep reference-router acknowledgement, durable local IOU registration, and evidence resolution distinct. Candidate self-tests demonstrate the candidate's local journal behavior; they do not qualify a Build 027 adapter or a persistent Closet router.

For a genuine Suit 4.0 runtime integration, recover the A–E runtime, original vectors, exact callable signature and required context/evidence builders. Design a versioned adapter around soso40e.pipeline.run_pipeline, using original 4.0 producers and canonical bytes. Exercise accepted, refused, replay dependency unavailable, and blocking-IOU liveness cases. Scope AUTH:0 to advisory outcomes throughout. The builder-reported 202 checks remain a historical evidence set.

The attached Build 027 runtime and experiment use deterministic fixtures and a local reference router. This evidence merge does not extend current results to external execution, deployment readiness, production Closet, Bridge-1 or remote exactly-once delivery.

Local runtime regression on Python 3.12.14: 594 full-suite passes and 52 targeted passes. These runs overlap and are reported separately.
