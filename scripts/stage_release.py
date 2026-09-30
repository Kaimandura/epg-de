"""Select successful main producers; download exact immutable Actions artifacts.

No fallback to checkout outputs or upstream feeds. Any failure leaves the live
Pages deployment alone. Both regions must pass before upload-pages-artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from release_bundle import policy_digest, verify
from release_policy import FILES, audit

WORKFLOWS = {"DE": "update-epg.yml", "USA": "update-usa-epg.yml"}


def trusted_run(run, repository, workflow):
    return (run.get("conclusion") == "success" and run.get("status") == "completed"
            and run.get("head_branch") == "main"
            and run.get("event") in {"push", "schedule", "workflow_dispatch"}
            and run.get("path") == f".github/workflows/{workflow}"
            and run.get("repository", {}).get("full_name") == repository
            and run.get("head_repository", {}).get("full_name") == repository)


def select_run(runs, repository, workflow):
    valid = [r for r in runs if trusted_run(r, repository, workflow)]
    if not valid:
        raise ValueError(f"no successful main producer for {workflow}")
    return max(valid, key=lambda r: r["id"])


class API:
    def __init__(self, repository, token):
        self.base = f"https://api.github.com/repos/{repository}"
        self.token = token

    def get(self, path, binary=False):
        request = urllib.request.Request(self.base + path, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "epg-release"})
        # urllib normally forwards authorization on redirects. Artifact storage
        # must never receive the GitHub token.
        class Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
                if urllib.parse.urlparse(newurl).scheme != "https":
                    raise ValueError("non-HTTPS artifact redirect")
                redirected.remove_header("Authorization")
                return redirected
        with urllib.request.build_opener(Redirect).open(request, timeout=180) as response:
            data = response.read()
        return data if binary else json.loads(data)


def extract_bundle(data, destination, region):
    wanted = set(FILES[region]) | {"manifest.json", "audit.json"}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) != len(wanted) or {e.filename for e in entries} != wanted:
            raise ValueError("artifact contains unexpected, duplicate, or missing paths")
        if sum(e.file_size for e in entries) > 1024 * 1024 * 1024:
            raise ValueError("artifact exceeds extraction size limit")
        destination.mkdir(parents=True)
        for entry in entries:
            if entry.is_dir() or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("artifact contains directory/symlink")
            with archive.open(entry) as source, (destination / entry.filename).open("wb") as target:
                shutil.copyfileobj(source, target)


def stage(api, repository, output, workspace, policy_hash):
    if output.exists() or workspace.exists():
        raise ValueError("staging destinations must be new")
    manifests = {}
    paths = {}
    aliases = []
    # Snapshot both selections first; no mutable latest-main files are read.
    runs = {region: select_run(api.get(f"/actions/workflows/{workflow}/runs?branch=main&status=success&per_page=100")["workflow_runs"],
                               repository, workflow) for region, workflow in WORKFLOWS.items()}
    for region, run in runs.items():
        artifacts = api.get(f"/actions/runs/{run['id']}/artifacts?per_page=100")["artifacts"]
        name = f"epg-{region.lower()}-{run['id']}-{run['run_attempt']}"
        matches = [a for a in artifacts if a["name"] == name and not a["expired"]]
        if len(matches) != 1:
            raise ValueError(f"missing/ambiguous validated artifact {name}")
        artifact = matches[0]
        data = api.get(f"/actions/artifacts/{artifact['id']}/zip", binary=True)
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if artifact.get("digest") != digest:
            raise ValueError(f"GitHub artifact digest mismatch: {name}")
        bundle = workspace / region
        extract_bundle(data, bundle, region)
        manifest = verify(bundle, region, {"repository": repository, "source_sha": run["head_sha"],
                                          "run_id": run["id"], "run_attempt": run["run_attempt"],
                                          "policy_sha256": policy_hash})
        manifests[region] = {**manifest, "artifact_id": artifact["id"], "artifact_digest": digest}
        aliases.extend(json.loads((bundle / "audit.json").read_text(encoding="utf-8"))["aliases"])
        paths.update({filename: bundle / filename for filename in FILES[region]})
    result = audit(paths, aliases)
    if result["status"] != "passed":
        raise ValueError("combined Pages release audit failed: " + json.dumps(result)[:5000])
    output.mkdir(parents=True)
    for name, path in paths.items():
        shutil.copyfile(path, output / name)
    (output / ".nojekyll").touch()
    (output / "release.json").write_text(json.dumps({"schema": 1, "producers": manifests,
                                                   "audit": result}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "epg-urls.txt").write_text("".join(f"https://{repository.split('/')[0].lower()}.github.io/{repository.split('/')[1]}/{name}\n"
                                                for name in sorted(paths)), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    stage(API(repository, os.environ["GH_TOKEN"]), repository, args.output, args.workspace, policy_digest(Path.cwd()))


if __name__ == "__main__":
    main()
