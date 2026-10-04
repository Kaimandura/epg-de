"""Seal validated producer bytes and verify them before staging Pages."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

from release_policy import FILES, audit, configured_aliases, validate_file_set

SCHEMA = 1


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def policy_digest(root):
    paths = [*Path(root, "scripts").glob("*.py"), *Path(root, "config").glob("*")]
    payload = [(p.relative_to(root).as_posix(), sha256(p)) for p in sorted(paths) if p.is_file()]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def seal(source, output, region, provenance, aliases=None):
    if output.exists():
        raise ValueError(f"bundle destination already exists: {output}")
    paths = {name: source / stem for name, stem in FILES[region].items() if (source/stem).is_file()}
    validate_file_set(paths,region)
    result = audit(paths, aliases)
    if result["status"] != "passed":
        raise ValueError("producer release gate failed: " + json.dumps(result, ensure_ascii=False)[:5000])
    output.mkdir(parents=True)
    for name, path in paths.items():
        shutil.copyfile(path, output / name)
    report = output / "audit.json"
    report.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    migration_path=source/'reconciliation.json'
    migration=json.loads(migration_path.read_text(encoding='utf-8')) if migration_path.exists() else {'mapping':[]}
    if migration_path.exists() and migration.get('output_sha256') != {name:sha256(path) for name,path in paths.items()}:
        raise ValueError('reconciliation proof does not match release bytes')
    (output/'migration.json').write_text(json.dumps(migration,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    manifest = {"schema": SCHEMA, "region": region, **provenance,
                "files": {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
                          for p in sorted(output.iterdir())}}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify(bundle, region, expected):
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or manifest.get("region") != region:
        raise ValueError("unsupported bundle schema/region")
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"bundle provenance mismatch: {key}")
    wanted = set(manifest.get('files',{}))
    validate_file_set(wanted,region,extra={'audit.json','migration.json'})
    if {p.name for p in bundle.iterdir()} != wanted | {"manifest.json"}:
        raise ValueError("unexpected/missing bundle entries")
    for name in wanted:
        path = bundle / name
        record = manifest["files"][name]
        if path.is_symlink() or not path.is_file() or record != {"sha256": sha256(path), "bytes": path.stat().st_size}:
            raise ValueError(f"bundle checksum mismatch: {name}")
    report = json.loads((bundle / "audit.json").read_text(encoding="utf-8"))
    if report.get("status") != "passed" or report.get("errors") or report.get("identity_violations"):
        raise ValueError("producer audit did not pass")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--region", choices=FILES, required=True)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument('--test-build',action='store_true')
    args = parser.parse_args()
    env = os.environ
    provenance = {"repository": env["GITHUB_REPOSITORY"], "source_sha": env["GITHUB_SHA"],
                  "run_id": int(env["GITHUB_RUN_ID"]), "run_attempt": int(env["GITHUB_RUN_ATTEMPT"]),
                  "policy_sha256": policy_digest(Path.cwd()), 'test_only':args.test_build}
    if (env.get("GITHUB_REF") != "refs/heads/main" and not args.test_build) or not re.fullmatch(r"[0-9a-f]{40}", provenance["source_sha"]):
        raise ValueError("production bundles require a main producer run")
    migration=json.loads((args.source/'reconciliation.json').read_text(encoding='utf-8'))
    if migration.get('status')!='passed' or migration.get('lkg',{}).get('status')!='passed':
        raise ValueError('production seal requires reconciled release and LKG proof')
    seal(args.source, args.output, args.region, provenance, configured_aliases(mapping=args.mapping))


if __name__ == "__main__":
    main()
