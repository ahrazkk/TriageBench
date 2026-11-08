## Axis A: failure_type ("what failed?")

Pick the category of the failure that ended the job (the first real error, not later fallout).

- `test_assertion`: tests ran and at least one failed: an assertion, an unexpected exception inside a test, a snapshot or golden-file mismatch, a test-runner "N failed" summary.
- `compile_error`: building the code failed: rustc/cargo build, gcc/clang, javac/gradle compile, go build/vet errors, a TypeScript or bundler build error.
- `type_error`: a static type checker failed (mypy, pyright, tsc --noEmit, flow). The code may still run.
- `lint_failure`: a linter, formatter or style/spelling/docs checker failed (ruff, flake8, black, ESLint, prettier, clippy with -D warnings, pre-commit hooks, codespell, markdownlint). Also static policy and security checks: security scanners (Trivy, gosec, CodeQL), dependency-review, changelog/PR-policy gates.
- `dependency_install`: installing or resolving dependencies failed (pip/uv/poetry resolution, missing wheel, npm/yarn/pnpm install, cargo fetch, go mod download, apt-get, 404 from a registry).
- `docker_build`: a `docker build`/buildx step failed because of the Dockerfile or the image build itself (bad instruction, missing build context, base image problem). If a command *inside* the image build fails (a dependency install, a compile, a lint), classify by that real error instead.
- `timeout_or_oom`: the job or a test hit a time limit, hung, was cancelled for time, or was killed for memory (exit 137, "Killed", OOM).
- `multi_failure`: two or more *independent* failure kinds in the same job (for example a lint error and an unrelated test failure).
- `ci_infra_or_config`: the CI machinery itself failed: invalid workflow YAML, a missing secret or permission, a failed checkout or action download, a lost runner, an upload/coverage/artifact step failing, a CI service's rate limit.

## Axis B: cause_class ("why, and whose problem is it?")

- `regression`: the change under test (the PR or pushed commit) broke it. The fix belongs in the source code.
- `flaky`: the test or code is non-deterministic by itself (race, timing, ordering, randomness, resource contention); the same code can pass on a retry.
- `environment`: something outside the repository changed or failed: a new release of a dependency, a runner image change, a network or service outage, an upstream API, a registry problem. A retry may pass once the outside problem goes away.
- `test_defect`: the test itself is wrong or out of date while the code is fine (the fix only changes tests or test data).
- `ci_config`: the workflow or its settings are wrong (the fix changes `.github/workflows/*`, CI scripts or CI settings).
- `unknown`: the evidence is not enough to decide. Allowed, and better than a confident guess.

Tie-breakers:
- Network/registry/service errors are `environment`, not `flaky`, unless the test itself is timing-dependent.
- A failure in code the change didn't touch, with a green history just before, points away from `regression`: check the history and the dependencies.
- A type or lint error introduced by the change is a `regression` (the change broke the build), even though no test failed.
