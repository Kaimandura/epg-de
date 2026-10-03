#!/usr/bin/env python3
"""Central read-only published release audit (never rewrites EPG data)."""
import argparse
import json
from pathlib import Path
from release_policy import FILES, audit, configured_aliases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('files', nargs='+', type=Path)
    parser.add_argument('--require-magenta-sports', action='store_true', help='Always enforced when DE-MAGENTA is present')
    parser.add_argument('--require-all-channels-active', action='store_true', help='Active channels are always required')
    parser.add_argument('--mapping', type=Path, help='USA producer alias provenance CSV')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if len({p.name for p in args.files}) != len(args.files):
        parser.error('duplicate input filenames')
    labels = {stem: name for region in FILES.values() for name, stem in region.items()}
    paths = {labels.get(p.name, p.name): p for p in args.files}
    if len(paths) != len(args.files):
        parser.error('duplicate release targets')
    if args.require_magenta_sports and 'DE-MAGENTA.xml.gz' not in paths:
        parser.error('--require-magenta-sports requires DE-MAGENTA')
    result = audit(paths, configured_aliases(mapping=args.mapping))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    for label, counts in result['outputs'].items():
        print(f"{label}: {counts}")
    for message in result['errors'][:30]:
        print('FAIL:', message)
    for item in result['identity_violations'][:30]:
        print('FAIL:', item)
    print(f"Release audit {result['status']}: {len(result['errors'])} errors, {len(result['identity_violations'])} identity violations")
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
