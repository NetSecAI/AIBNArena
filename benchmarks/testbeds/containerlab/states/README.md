# Reference states
Reference-state JSON contains reviewed, idempotent provisioning commands applied after deployment and before healthy-state oracle evaluation. It is not an oracle and is never sent to a SUT.
Each testbed config names the exact state path. Result provenance records its SHA-256 hash.
