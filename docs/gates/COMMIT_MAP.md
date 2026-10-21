# Commit map

The history was rewritten once, just before the first push, to remove the `Co-Authored-By` trailers from the commit
messages. Nothing else changed: trees, authors, author dates, committers, committer dates and subjects are identical.
The gate logs and `STATE.md` refer to the **old** hashes; this table maps each to the hash in the published
history.

| Old hash | New hash | Subject |
|---|---|---|
| `3a088bc` | `a82c2fc` | chore: initial repository structure and gitignore |
| `d7bee0a` | `404f052` | chore: keep fixtures and JSON byte-exact with .gitattributes |
| `a2dcd3e` | `7013b37` | chore: establish master blueprint, compiler pipeline, and state orrery |
| `38bccc8` | `395afae` | docs(G0): attest track posture |
| `b8b1303` | `f1dc1ad` | chore(G0): environment, pins, subagent routing and verified constraints |
| `bf7ffee` | `33c0ebb` | test(G1): orrery fixture export and synthetic Ring and Bee fixtures |
| `bf1bf21` | `a59cf2d` | feat(G1): HW-JCS contracts, hash-chained ledger, Ring and Bee ingest |
| `c19c0a0` | `cfe0152` | docs(G1): completion log |
| `2f5ad88` | `0a42290` | docs(G0): record developer attestations for console, bedrock, and cognito |
| `813e6d6` | `4a11bfb` | feat(G2): Streamable HTTP MCP server, RFC 8414 OAuth facade, and S+ tools |
| `4d7506b` | `3704707` | docs(G2): completion log and state snapshot |
| `99a0dbb` | `fe709af` | docs(G2): record alexa discovery summary attestation |
| `273b995` | `513bdee` | docs(G2): update state and log to reflect G2 PASS |
| `c4253a1` | `ea497c2` | feat(G3): CDK infrastructure stack, topology definitions, and endpoint probe suite |
| `6decd1e` | `6576216` | docs(G3): completion log and state snapshot |
| `ac5d770` | `d47659e` | feat(G4): Causal engine, GNOMON adapter, and SPRT decision core |
| `f93721c` | `07688bb` | docs(G4): completion log, state snapshot and the submodule blocker |
| `d0eae7b` | `3945893` | feat(G4): link vendor/gnomon submodule |
| `3b95867` | `8cca910` | docs(G4): update state, log, and blockers to reflect G4 PASS |
| `9ac7000` | `ead9ad3` | feat(G5): AXIOM broker, HX invariants parity, and Step Functions ladder |
| `a1dff48` | `7e3e65d` | docs(G5): completion log and state snapshot |
| `dd88090` | `cc3a6d4` | feat(G6): KMS decision attestation, offline judge verifier, and web approval lockfile |
| `4c356a7` | `025bd27` | docs(G6): completion log and state snapshot |
| `a30f831` | `7ad18d1` | docs(G7): README, MIT license, friction log and product feedback |
| `564c61e` | `4ab0463` | docs(G7): completion log and state snapshot |
| `a4fb731` | `631036f` | docs(G7): update architectural lineage, repo links, and submission window disclosure |
| `48cb377` | `7ac04ab` | docs(G7): record the README lineage as complete in the state and log |
| `a77d36c` | `77898f4` | docs: record that commit trailers are removed from the history before publishing |

(The last row is the commit that records this rewrite in the docs; this file was added after it.)
