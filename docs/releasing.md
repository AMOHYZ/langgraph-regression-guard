# Releasing

`release.yml` publishes to TestPyPI, then PyPI, with trusted publishing (OIDC,
no token). Set up once, before the first tag (done for 0.1.0), on test.pypi.org:

1. Sign in, then **Your account → Publishing**
   (https://test.pypi.org/manage/account/publishing/). The account needs 2FA: without
   it, TestPyPI shows its 2FA setup page instead, and no publisher can be added.
2. Under **Add a new pending publisher**, on the GitHub tab: TestPyPI project name `lgcov`,
   owner `AMOHYZ`, repository name `langgraph-regression-guard`, workflow name
   `release.yml`, environment name `testpypi`.
3. Click **Add**. The first publish makes it the project's own publisher, so later tags
   need nothing more on TestPyPI. A publish that fails with `invalid-publisher` found no
   publisher there matching the claims its log prints.

PyPI has its own job, `publish-pypi`, which runs after the TestPyPI one. Set up once, before
the first tag that should reach PyPI:

1. On pypi.org, use a personal account with a personal email, not a work one, with 2FA on.
   It is a separate account from the TestPyPI one.
2. **Your account → Publishing** (https://pypi.org/manage/account/publishing/) → **Add a
   new pending publisher**, GitHub tab: PyPI project name `lgcov`, owner `AMOHYZ`,
   repository name `langgraph-regression-guard`, workflow name `release.yml`, environment
   name `pypi`. A pending publisher doesn't reserve the name: only the first upload does.
3. On GitHub, **Settings → Environments → New environment** `pypi`, with yourself under
   **Required reviewers**. Each release then waits there for an approval before it uploads.

A version on PyPI can't be uploaded again, so the approval is the last check.

Then pushing a `vX.Y.Z` tag builds, makes the GitHub Release and publishes. Run by hand
(Actions → release → Run workflow), `dry_run` (on by default) only builds the wheel and
sdist, checks the wheel installs and runs, and uploads them as the `dist` artifact.
