# Publishing TokenCast

A copy-pasteable runbook for a human shipping TokenCast publicly: (a) push to GitHub,
(b) publish to PyPI, (c) host `tokencast.html`. Do them in that order.

TokenCast ships two console scripts — `tokencast` (the stdlib-only light tier) and
`tokencast-optimize` (the heavy tier, behind the `[optimize]` extra). The light tier has no
third-party deps; the optimize tier needs `claude-agent-sdk` + `pyyaml` and an Anthropic API key
to run live.

> Fill-in markers used below: replace **`OWNER`** with your GitHub username/org everywhere, and
> **`<PYPI_TOKEN>`** with a PyPI API token (starts with `pypi-`). These are the only two values
> you must supply.

---

## (a) Publish to GitHub

This is already a git repo, so you only need to create the remote and push.

1. Make sure `pyproject.toml` matches what you're shipping **before** you tag:
   - `version` should be `0.3.0` (the release you're tagging).
   - `[project.urls]` should point at `https://github.com/OWNER/tokencast` (Homepage, Repository,
     and ideally Issues). If those keys are missing or still say `OWNER`, fix them first — the
     sibling packaging change owns `pyproject.toml`.

2. Commit anything outstanding (skip if your working tree is clean):

   ```sh
   git add -A
   git commit -m "chore: prepare v0.3.0 release"
   ```

3. Create the public repo and set the remote. Easiest with the GitHub CLI:

   ```sh
   gh repo create OWNER/tokencast --public --source=. --remote=origin --push
   ```

   If you prefer to do it by hand instead (create the empty repo in the GitHub UI first):

   ```sh
   git remote add origin https://github.com/OWNER/tokencast.git
   git branch -M main
   git push -u origin main
   ```

4. Tag the release and push the tag:

   ```sh
   git tag -a v0.3.0 -m "TokenCast v0.3.0"
   git push origin v0.3.0
   ```

5. (Optional) Cut a GitHub Release from the tag:

   ```sh
   gh release create v0.3.0 --title "v0.3.0" --notes-file CHANGELOG.md
   ```

> Keep the git tag (`v0.3.0`) and `pyproject.toml`'s `version` (`0.3.0`) in lockstep with the
> version you upload to PyPI in step (b).

---

## (b) Publish to PyPI

The package name is `tokencast`. It exposes the `tokencast` and `tokencast-optimize` console
scripts; the optimize tier's deps install via the `[optimize]` extra.

1. Install the build + upload tooling:

   ```sh
   python -m pip install build twine
   ```

2. Build the sdist + wheel into `dist/` (these are gitignored — do not commit them):

   ```sh
   python -m build
   ```

3. Validate the artifacts:

   ```sh
   python -m twine check dist/*
   ```

4. **Dry run on TestPyPI first.** Upload there, then install from it in a clean venv to confirm
   it works end-to-end before touching real PyPI:

   ```sh
   python -m twine upload --repository testpypi dist/*
   # then, in a throwaway environment:
   python -m pip install --index-url https://test.pypi.org/simple/ \
     --extra-index-url https://pypi.org/simple/ tokencast
   ```

   (The `--extra-index-url` lets pip resolve real dependencies from PyPI while pulling `tokencast`
   itself from TestPyPI.)

5. Upload to real PyPI with your API token:

   ```sh
   python -m twine upload dist/* --username __token__ --password <PYPI_TOKEN>
   ```

   (You can also export `TWINE_USERNAME=__token__` and `TWINE_PASSWORD=<PYPI_TOKEN>` instead of
   passing them on the command line.)

6. Verify the published package from a clean machine:

   ```sh
   pipx install tokencast
   tokencast forecast --help

   # zero-install run via uv:
   uvx tokencast forecast --help

   # the heavy optimize tier (pulls claude-agent-sdk + pyyaml):
   pipx install "tokencast[optimize]"
   tokencast-optimize --help
   ```

   Note: `tokencast-optimize`'s live commands need an Anthropic API key (`ANTHROPIC_API_KEY`) to
   actually call a model; `--help` works without one.

> **Re-publishing rule:** PyPI will not let you overwrite an already-uploaded version. To publish
> again you MUST bump the version in `pyproject.toml` (and re-tag in git), then rebuild and
> re-upload. Delete the stale `dist/` first (`rm -rf dist/`) so you don't re-upload old artifacts.

---

## (c) Host `tokencast.html`

`tokencast.html` is a single, dependency-free file. It parses Claude Code logs **entirely in the
browser** — nothing is uploaded to any server. State that on the page as a trust point: your
JSONL never leaves your machine.

The simplest host is GitHub Pages:

1. In the repo on GitHub: **Settings → Pages**. Under "Build and deployment", set Source to
   **Deploy from a branch**, branch **`main`**, folder **`/ (root)`**, then Save.

2. After it builds, the file is live at:

   ```
   https://OWNER.github.io/tokencast/tokencast.html
   ```

   Both the folder/file picker and the "Load demo data" button work as-is over HTTPS.

Alternatives, all equally fine since it's just one static file:

- **Any static host** (Netlify, Vercel, Cloudflare Pages, S3, etc.) — drop `tokencast.html` in and
  serve it.
- **Local / LAN** for a quick look:

  ```sh
  python -m http.server 8000
  # then open http://localhost:8000/tokencast.html
  ```

No build step, no bundler, no server-side code is needed in any case.
