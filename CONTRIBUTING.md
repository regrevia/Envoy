# Contributing To Envoy

## Branches

- `dev`: active development, platform adapters, experiments, and fixes.
- `main`: latest accepted release for users.

Users should use `main`. Contributors should branch from `dev` or work directly on a short-lived feature branch based on `dev`.

## Delivery Flow

1. Develop and test on `dev`.
2. Run the focused tests for the changed area.
3. Run the frontend production build when UI changes are included.
4. Verify secrets, local runtime files, media, databases, and generated exports are not tracked.
5. Merge the accepted `dev` state into `main`.
6. Push `main` and publish a release tag such as `v0.1.0`.

The `main` branch must remain runnable by a new user with the documented Docker or Windows startup instructions.

## Local Checks

```bash
backend/.venv/bin/pytest -q
cd frontend && npm run build
cd ..
docker compose config
git diff --check
```

Before pushing:

```bash
git status --short
git ls-files | rg '(^|/)(tmp|Check|\.env$|.*\.db$|.*\.enc$|.*\.key$|.*\.mp4$|.*\.cookies)'
```

Never commit API keys, browser cookies, private model endpoints, local databases, downloaded videos, generated exports, or encrypted runtime configuration.

## Release Notes

Every `main` update should state:

- the user-visible changes;
- supported startup methods;
- verification results;
- known platform or network limitations.
