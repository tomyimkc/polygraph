# Independent reproduction

Polygraph welcomes a reproduction from anyone who is not a project contributor. A real outside
receipt is more valuable than another claim from the author, so this page makes the smallest useful
test explicit and keeps the result public.

## Fast path: reproduce the product idea

This path does not need Arm hardware or a model download:

```bash
git clone https://github.com/tomyimkc/polygraph
cd polygraph
git rev-parse HEAD
make demo
```

Expected outcome:

- the "liar" binary prints that its fast path is enabled, but Polygraph returns mismatch;
- the "honest" binary prints the same claim, enters the fast function, and Polygraph returns match;
- the command exits successfully only when both expected verdicts are observed.

## Submit a public receipt

Open an
[independent reproduction report](https://github.com/tomyimkc/polygraph/issues/new?template=independent-reproduction.yml)
and include:

1. the exact Polygraph commit from `git rev-parse HEAD`;
2. operating system and CPU architecture;
3. compiler and debugger versions;
4. the complete `make demo` output;
5. whether you have contributed code or data to Polygraph.

Please report failures too. A failed or unclear reproduction is useful evidence and will not be
edited into a success.

## Real Arm / llama.cpp reproduction

The full KleidiAI verifier needs an Arm64 host, a pinned `llama.cpp` build, a GGUF model, and
`lldb` or `gdb`. Start with [`docs/QUICKSTART.md`](QUICKSTART.md) and attach the generated JSON
ledger to the same issue form. Never upload a model file or secret; record its public source and
checksum instead.

## Current status

Project CI and the author-controlled Arm receipts are public. **No outside person is represented as
an independent reproducer until they post their own public receipt.**
