# Contributing to OpenTapoVac

Contributions are mostly welcome (but do tell if you've used AI or other tools).  If the length of this text scares you, then I'd rather you skip reading and just make a pull request on GitHub.  If you find it too difficult to write test code, etc., then you may skip it and hope the maintainer will fix it.

## What to include

Every submission should ideally include:

- **Test code** covering the new behaviour or bug fix
- **Documentation** updates where relevant
- **A changelog entry** in `CHANGELOG.md` under `[Unreleased]`

`make dev` installs the package editable with the test tools, `make test`
and `make lint` run the checks.  No robot is needed for the tests.

## Commit messages

Please follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) and write messages in the imperative mood:

- `fix: wait for the dock before sending the next run`
- `feat: add a preset button`
- `docs: update README`

Rather than:

- `This commit fixes the queue`
- `Added new button`

Note: older commits in this repository predate this convention and do not follow it.
