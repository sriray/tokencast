# Publishing TokenCast

A copy-pasteable runbook for a human shipping TokenCast publicly: (a) push to GitHub,
(b) publish to PyPI, (c) host `tokencast.html`. Do them in that order.

TokenCast ships two console scripts — `tokencast` (the stdlib-only light tier) and
`tokencast-optimize` (the heavy tier, behind the `[optimize]` extra). The light tier has no
third-party deps; the optimize tier needs `claude-agent-sdk` + `pyyaml` and an Anthropic API key
to run live.

> This repo is already wired for the GitHub account **`sriray`** (it's in `pyproject.toml`'s
> `[project.urls]` and the commands below). If you're shipping under a different user or org,
> search-and-replace `sriray` everywhere first. The one secret you must supply is **`<PYPI_TOKEN>`**
> — a PyPI API token (starts with `pypi-`).

---

## (a) Publish to GitHub

This is already a git repo, so you only need to create the remote and push.

1. Make sure `pyproject.toml` matches what you're shipping **before** you tag:
   - `version` should be `0.3.0` (the release you're tagging).
   - `[project.urls]` should point at `https://github.com/sriray/tokencast` (Homepage, Repository,
     and Issues). These are already set; only change them if you're shipping under a different
     account/org.

2. Commit anything outstanding (skip if your working tree is clean):

   ```sh
   git add -A
   git commit -m "chore: prepare v0.3.0 release"
   ```

3. Create the public repo and set the remote. Easiest with the GitHub CLI:

   ```sh
   gh repo create sriray/tokencast --public --source=. --remote=origin --push
   ```

   If you prefer to do it by hand instead (create the empty repo in the GitHub UI first):

   ```sh
   git remote add origin https://github.com/sriray/tokencast.git
   git branch -M main
   git push -u origin main
   ```

4. Tag the release and push the tag:

   ```sh
   git tag -a v0.3.0 -m "TokenCast v0.3.0"
   git push origin v0.3.0
   ```

5. (Optional) Cut a GitHub Release from the tag. A ready-to-use, tailored notes file ships at
   `docs/RELEASE_NOTES_v0.3.0.md`:

   ```sh
   gh release create v0.3.0 --title "TokenCast v0.3.0" --notes-file docs/RELEASE_NOTES_v0.3.0.md
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

This repo ships a Pages deploy workflow (`.github/workflows/pages.yml`) that publishes
`tokencast.html` automatically. It stages the file as `index.html` (so the bare Pages URL serves
the tool) and also keeps it at `/tokencast.html`.

1. One-time: in the repo on GitHub, **Settings → Pages → Build and deployment**, set Source to
   **GitHub Actions**, then Save. (This is the "GitHub Actions" source, *not* "Deploy from a
   branch" — the bundled workflow does the deploy.)

2. The workflow runs on every push to `main` that touches `tokencast.html`, and you can also
   trigger it by hand from the **Actions** tab → "Deploy tokencast.html to GitHub Pages" → **Run
   workflow**. After the first successful run, the tool is live at:

   ```
   https://sriray.github.io/tokencast/            # bare URL — serves the app
   https://sriray.github.io/tokencast/tokencast.html
   ```

   Both the folder/file picker and the "Load demo data" button work as-is over HTTPS.

> Prefer no workflow? You can instead set Source to **Deploy from a branch** (`main`, `/ (root)`);
> the file is then served at `https://sriray.github.io/tokencast/tokencast.html`. If you go this
> route, delete `.github/workflows/pages.yml` so the two mechanisms don't fight.

Alternatives, all equally fine since it's just one static file:

- **Any static host** (Netlify, Vercel, Cloudflare Pages, S3, etc.) — drop `tokencast.html` in and
  serve it.
- **Local / LAN** for a quick look:

  ```sh
  python -m http.server 8000
  # then open http://localhost:8000/tokencast.html
  ```

No build step, no bundler, no server-side code is needed in any case.
