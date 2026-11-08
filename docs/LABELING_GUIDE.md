# TriageBench labelling guide (v1)

Every case gets two labels, a root cause and evidence lines. The model prompt uses the same
definitions (`prompts/labels_v1.md`), so the guide and the prompt never drift apart.

## What you look at

`triagebench review` shows, for each case:

1. **Metadata**: repo, workflow, job, trigger, branch, failed step.
2. **The log excerpt** (hybrid reduction, with line numbers).
3. **The change under test** (files changed by the PR or commit).
4. **Run history** before the failure (same workflow, same branch and the default branch).
5. **Hints from the future** (the systems under test never see these):
   - `rerun_succeeded`: a later attempt of the *same run* (same commit) passed.
   - `next_success_sha` / `next_success_same_sha`: the next green run of this workflow on this branch.
   - `fix_files`: files changed between the failing commit and that next green commit.
   - `same_day_repo_failures`: how many runs failed in this repo that day.

Hints are evidence, not answers. A re-run can pass because an outage ended (environment, not
flaky); fix files can include unrelated commits.

## Decision tree

**Axis A (`failure_type`)**: find the *first real error* (not later fallout such as "Process
completed with exit code 1"), then:

1. Did the CI machinery fail before or around the project's own commands (checkout, action
   download, invalid YAML, missing secret, artifact/coverage upload, lost runner)? → `ci_infra_or_config`
2. Did the job hit a time limit, hang, or get killed for memory? → `timeout_or_oom`
3. Did dependency installation/resolution fail? → `dependency_install`
4. Did a `docker build` fail because of the Dockerfile or image build itself? → `docker_build`
   (a dependency install, compile or lint failing *inside* the build is classified by that error)
5. Did compilation/building fail? → `compile_error`
6. Did a static type checker fail? → `type_error`
7. Did a linter/formatter/style check, or a static security/policy check (Trivy, gosec,
   dependency-review, PR-policy gate), fail? → `lint_failure`
8. Did tests run and fail? → `test_assertion`
9. Two or more of 5–8 failed independently in this job? → `multi_failure`

**Axis B (`cause_class`)**:

1. Did the fix (next green commit) only touch `.github/workflows/*` or CI scripts, or is the error
   clearly a CI setting? → `ci_config`
2. Is the error from outside the repo (registry 5xx, network, new dependency release, runner
   image, upstream API), especially if many unrelated runs failed the same day? → `environment`
3. Did a re-run of the same commit pass, and the failure looks timing/order/randomness related
   inside the test? → `flaky`
4. Did the fix only change tests or test data, and the test was wrong? → `test_defect`
5. Did the change under test introduce the error (type/lint/compile/test failure in code it
   touched or broke), with a green history before it? → `regression`
6. Otherwise → `unknown`. It's allowed and better than a guess.

## Evidence and root cause

- **Evidence**: 1–5 line numbers of the cleaned log that prove the labels. Prefer the first real
  error line and the line that names the failing test/file.
- **Root cause**: at most 3 sentences: what failed, where (file/test/package), and why if known.
- **Duplicate group**: if two cases share one underlying issue (same flaky test, same broken
  dependency release), give them the same short group name (e.g. `httpx-h2-0.4.2-break`).

## Label provenance (honesty rules)

- Labels drafted by an AI model are stored with `ai_drafted: true`, `verified: false`.
- A label counts as human-verified only after Ahraz reviews it in `triagebench review` (accepting
  or correcting it), which sets `verified: true` and `labeller: ahraz`.
- Results always report how many labels were verified. Numbers on unverified labels are
  "preliminary".
- Self-agreement: a week after labelling, relabel 30 random cases blind and report Cohen's κ.
