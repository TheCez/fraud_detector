---
name: publish-repo
description: Create a brand-new GitHub repository for a local project directory and push it, with a secret-leak check before the first commit ever leaves the machine. Use when asked to "put this on GitHub", "make this a repo and push it", or to publish an unversioned local project. Windows/PowerShell + gh CLI.
---

# Publish a local project as a new GitHub repo

Order matters: everything that could leak a secret is checked **before** the first push, because a pushed commit is public history even if you delete it afterwards.

## 1. Establish the starting state

Never assume the directory is unversioned - or that it isn't.

```bash
git rev-parse --is-inside-work-tree 2>&1   # "fatal: not a git repository" = fresh
git remote -v                               # a remote already? stop and ask
gh auth status                              # confirm which account will own the repo
```

A `.git/` directory can exist and still be empty from a failed `git init` - `rev-parse` is the check, not `ls`.

Confirm the target account from `gh auth status` rather than guessing from the folder path. Report it to the user if it differs from what they said.

## 2. Write the ignore rules before staging anything

Ensure `.gitignore` covers, at minimum: `.env`, `.env.local`, virtualenvs (`.venv/`, `venv/`), `node_modules/`, build output, tool caches (`.pytest_cache/`, `__pycache__/`), IDE and OS files, and any local runtime/data directory.

## 3. Audit the exact file list - not the directory tree

```bash
git init -b main
git add -A -n | sed 's/^add //' | tr -d "'" | sort
```

Read every path in that list. Dependency directories inflate `du` output to gigabytes while contributing nothing to the commit, so file-list review is the only meaningful check.

Block on anything matching: `.env`, credentials, tokens, keys, certificates, private datasets, customer data, or large binaries that belong in LFS.

## 4. Get an explicit visibility decision

Ask the user private vs public with `AskUserQuestion`. Do not default silently.

Name any content that becomes world-readable under `public` - sample datasets, fixtures, answer keys, internal docs - so the choice is informed. If the user picks public anyway, that is their call: proceed and state plainly what is now public.

## 5. Commit

```bash
git add -A
git commit -m "<message>"
```

Do not add an agent name as co-author.

## 6. Create the remote and push

`gh repo create` in one shot - it creates, wires the remote, and pushes:

```bash
gh repo create <name> --public --source=. --remote=origin --push
```

Swap `--public` for `--private` per step 4. Add `--description "..."` when the project has a one-liner worth showing.

If the repo already exists, `gh repo create` fails rather than clobbering it. That is the desired behaviour - surface the error, do not force.

## 7. Verify the push landed

Do not report success from the create command's exit code alone.

```bash
git status -sb                                    # confirm main...origin/main, nothing ahead
gh repo view <owner>/<name> --json name,visibility,url,defaultBranchRef
```

Then confirm the secret file is absent from the pushed tree:

```bash
git ls-files | grep -E '(^|/)\.env$' && echo "LEAK" || echo "clean"
```

Report the URL, the visibility, and the file count actually pushed.
