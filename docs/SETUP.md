# Setup (beginner steps)

## 1. Create the repo
1. On GitHub click **+ → New repository**.
2. Name it **exactly** `JUST-TWILIGHT-VRS` (same as your username). Set it to **Public**.
3. Tick **Add a README** (you will replace it), then **Create repository**.

## 2. Upload the files
1. Unzip the download.
2. In the new repo click **Add file → Upload files** and drag in everything inside the unzipped folder:
   `README.md`, `config.json`, `assets/`, `scripts/`, `docs/`.
3. Commit to `main`.
4. The workflow file lives in a hidden folder, so upload it separately:
   **Add file → Create new file**, type `.github/workflows/update-profile.yml` as the name
   (typing `/` makes folders), paste the contents of that file, and commit.

## 3. Allow the workflow to push
**Settings → Actions → General → Workflow permissions → Read and write permissions → Save.**

## 4. Token (optional)
The built-in `GITHUB_TOKEN` is enough for public data, so you can skip this.
Only if you want private contributions counted:
1. Turn on **Include private contributions on my profile** in your profile settings.
2. **Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token**.
   Scope: only `read:user`.
3. In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
   Name: `PROFILE_TOKEN`, value: the token.

## 5. Run it the first time
1. Open the **Actions** tab → **Update profile assets** → **Run workflow**.
2. Wait about a minute for the green tick. It commits fresh SVGs into `assets/`.
3. After that it runs by itself every day.

## 6. Make it yours
- Edit `config.json` (role, stack, project tags and descriptions, `utc_offset` for the commit-hour chart).
- Project links: edit the two `<a href>` lines in `README.md`, and the `button` text in `config.json`.
- Run `python scripts/build.py --offline` locally to rebuild the static cards (needs `pip install pillow`).

# Final checklist
- [ ] Workflow run is green in the Actions tab
- [ ] Every image on your profile page loads (no broken-image icons)
- [ ] Repos, stars, contributions and followers match between the HUD, the stats card and the summary
- [ ] Phone view: cards scale down, project cards stack, nothing overflows
- [ ] Project buttons point where you want them
- [ ] No "waiting for first workflow run" placeholders left
- [ ] Profile views badge shows (it is the one third-party widget)
