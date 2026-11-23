# SoSoSuit 4.2

A standalone governance candidate derived from the recovered SoSo Suit 4.0 architecture. Python 3.11+ and SQLite, with no external runtime packages.

The host commits task context, permitted actions, principal, build identity and evidence digests before requesting a candidate. The candidate binds to that state. The evaluator enforces closed actions and typed IOUs, requires provenance rooted in committed external evidence, and emits an advisory disposition and a non-executable seal. Replay recomputes the result given the original inputs. Journal submission atomically persists the verdict and accepted IOUs. Exact retries deduplicate by receipt identity. IOU closure is an immutable advisory event belonging to the original principal. EVIDENCE_RECEIVED records a host report and does not validate evidence.

The trusted host supplies the authenticated principal, original evidence and allowed actions. The CLI is local and assumes control of its inputs and SQLite file. Digests identify and bind artifacts. A digest seal is not a signature; protect the original inputs and journal through the host. The journal stores receipts and IOUs. Preserve the candidate and context separately for replay.

## Run the fixture

From the extracted directory:

```sh
work=$(mktemp -d)
python -m sososuit42 commit examples/context.json > "$work/state.json"
python -m examples.make_candidate > "$work/candidate.json"
python -m sososuit42 submit examples/context.json "$work/candidate.json" --db "$work/suit.sqlite" > "$work/receipt.json"
python -m sososuit42 replay examples/context.json "$work/candidate.json" "$work/receipt.json"
python -m sososuit42 pending local-operator --db "$work/suit.sqlite"
python -m unittest discover -s tests -v
```

Use the decision_id and IOU id from pending to record a terminal event:

```text
python -m sososuit42 complete PRINCIPAL DECISION_ID IOU_ID EVIDENCE_RECEIVED NOTE --db DATABASE
```

## Lineage and scope

The nine unchanged recovered 4.0 schemas under reference/ are design evidence. 4.2 uses its own wire objects and private canonical profile. It requires NO_OP in each context as an explicit escape action. REQUEST_DISCRIMINATOR is recognized in policy and refused for advisory emission. Claim dependencies must be ordered and rooted in ext: evidence. The local journal implements durable pending and terminal events. The original 4.0 conflict graph and role matrix, external provenance transformation objects, distributed authority, authenticated transport and Build 028 runtime integration are beyond this candidate's tested scope. The original frozen 4.0 source and test vectors are still needed for exact compatibility qualification.

AUTH:0 and NOT_GRANTED apply to every disposition. No execution or evidence promotion is performed.
