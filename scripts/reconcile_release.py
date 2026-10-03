"""Resolve proven identities into one owner, preserving programme metadata.

Inputs are never overwritten. The approved owner controls occupied time slots;
complete alternative records are retained as evidence. Invalid explicit durations
are quarantined. Equivalent records merge metadata; gaps retain complementary data.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from release_policy import FILES, MAGENTA_REQUIRED, audit, configured_aliases, identity_groups, inspect, timestamp, validate_file_set


def elements(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rb') as stream:
        iterator = ET.iterparse(stream, events=('start', 'end'))
        _, root = next(iterator)
        if root.tag != 'tv':
            raise ValueError(f'{path}: expected tv root')
        for event, node in iterator:
            if event == 'end' and node.tag in {'channel', 'programme'}:
                yield node
                root.remove(node)


def merge_metadata(target, source):
    """Retain every distinct XML subtree. Never replace richer descriptions."""
    def normalize(node):
        node.tail = None
        if node.text:
            node.text = node.text.strip()
        for child in node:
            normalize(child)
    normalize(target)
    normalize(source)
    keys = {ET.tostring(child) for child in target}
    for child in source:
        key = ET.tostring(child)
        if key not in keys:
            target.append(child)
            keys.add(key)
    for key, value in source.attrib.items():
        if key not in target.attrib:
            target.set(key, value)
        elif key not in {'id', 'channel', 'start', 'stop'} and target.get(key) != value:
            raise ValueError(f'conflicting programme attribute {key}')
    return target


def choose(group, region, official):
    def score(c):
        if region == 'USA':
            return (c.id not in official, '@' not in c.id, -c.count, c.file, c.id)
        # User-approved ownership: Magenta linear, original Pluto, then original
        # native linear/master, Samsung, and only genuinely independent Amazon.
        rank = {'DE-MAGENTA.xml.gz': 0, 'DE-PLUTO.xml.gz': 1,
                'DE-MASTER.xml.gz': 2, 'DE-SAMSUNG.xml.gz': 3, 'DE-AMAZON.xml.gz': 4}
        return (rank[c.file], c.id not in MAGENTA_REQUIRED, '@HD' not in c.id, -c.count, c.id)
    winner = min(group, key=score)
    targets = [winner.file]
    if region == 'DE' and winner.file != 'DE-MASTER.xml.gz' and any(c.file == 'DE-MASTER.xml.gz' for c in group):
        targets.append('DE-MASTER.xml.gz')
    return winner, targets


def reconcile(paths, output, region, mapping=None):
    if output.exists():
        raise ValueError(f'output must be a new directory: {output}')
    validate_file_set(paths, region)
    aliases = configured_aliases(mapping=mapping)
    official = set()
    if mapping:
        with mapping.open(encoding='utf-8-sig', newline='') as stream:
            official = {r['xmltv_id'] for r in csv.DictReader(stream)}
    channels = []
    for label, path in paths.items():
        rows, errors, _ = inspect(path, label)
        # Quarantine invalid explicit intervals, but no other integrity error
        # is a licence to modify data or to ignore a missing required channel.
        fatal = [e for e in errors if 'non-positive programme duration' not in e]
        if fatal:
            raise ValueError('\n'.join(fatal[:20]))
        channels.extend(rows)
    groups, evidence = identity_groups(channels, aliases)
    plan, destinations, canonical = {}, {}, {}
    for number, group in enumerate(groups):
        winner, targets = choose(group, region, official)
        destinations[number] = targets
        canonical[number] = winner.id
        for c in group:
            plan[(c.file, c.id)] = number
    output.mkdir(parents=True)
    connection = sqlite3.connect(output / 'reconciliation.sqlite')
    connection.execute('CREATE TABLE programmes (identity INTEGER, start TEXT, stop TEXT, title TEXT, xml BLOB, origin TEXT, PRIMARY KEY(identity,start,stop))')
    connection.execute('CREATE INDEX programme_identity ON programmes(identity,start)')
    channel_nodes = {}
    quarantined = 0
    merged = 0
    alternatives_count = 0
    input_programmes = 0
    conflicts = []
    with (output / 'quarantine.jsonl').open('w', encoding='utf-8') as quarantine, (output / 'schedule-alternatives.jsonl').open('w', encoding='utf-8') as alternatives:
        ranked_paths = sorted(paths.items(), key=lambda item: {'DE-MAGENTA.xml.gz':0,'DE-PLUTO.xml.gz':1,'DE-MASTER.xml.gz':2,'DE-SAMSUNG.xml.gz':3,'DE-AMAZON.xml.gz':4}.get(item[0],10))
        for label, path in ranked_paths:
            for node in elements(path):
                cid = node.get('id') if node.tag == 'channel' else node.get('channel')
                identity = plan[(label, cid)]
                if node.tag == 'channel':
                    node.set('id', canonical[identity])
                    if identity in channel_nodes:
                        merge_metadata(channel_nodes[identity], node)
                    else:
                        channel_nodes[identity] = ET.fromstring(ET.tostring(node))
                    continue
                input_programmes += 1
                original_xml = ET.tostring(node,encoding='unicode')
                start = timestamp(node.get('start'))
                stop = timestamp(node.get('stop')) if node.get('stop') else None
                if stop is not None and stop <= start:
                    quarantine.write(json.dumps({'file': label, 'reason': 'non-positive duration',
                                                  'source_xml': original_xml}) + '\n')
                    quarantined += 1
                    continue
                start_key = start.strftime('%Y%m%d%H%M%S +0000')
                stop_key = stop.strftime('%Y%m%d%H%M%S +0000') if stop else ''
                title = (node.findtext('title') or '').strip().casefold()
                node.set('channel', canonical[identity])
                node.set('start', start_key)
                if stop:
                    node.set('stop', stop_key)
                key = (identity, start_key, stop_key)
                origin = label + ':' + cid
                prior = connection.execute('SELECT xml,origin,title FROM programmes WHERE identity=? AND start=? AND stop=?', key).fetchone()
                if prior:
                    merged += 1
                    node = merge_metadata(ET.fromstring(prior[0]), node)
                    origin, title = prior[1], prior[2]
                else:
                    # An authoritative owner keeps its own timed schedule. A
                    # secondary copy can supplement gaps, not change an occupied
                    # time slot. Preserve the complete alternative record and
                    # its chosen source in the migration evidence.
                    other = connection.execute('SELECT start,stop,title,origin FROM programmes WHERE identity=? AND start<? AND stop>? AND origin!=?', (identity, stop_key or start_key, start_key, origin)).fetchall()
                    for existing in other:
                        conflicts.append({'id': canonical[identity], 'new': [label,cid,start_key,stop_key,title], 'existing': list(existing)})
                    if other:
                        alternatives_count += 1
                        alternatives.write(json.dumps({'canonical_id':canonical[identity], 'source_file':label,'source_id':cid,
                                                       'reason':'owner schedule has priority for overlapping interval',
                                                       'retained_intervals':other, 'source_xml':original_xml},ensure_ascii=False)+'\n')
                        continue
                payload = ET.tostring(node, encoding='utf-8')
                connection.execute('INSERT OR REPLACE INTO programmes VALUES(?,?,?,?,?,?)', (*key,title,payload,origin))
            connection.commit()
    kept = connection.execute('SELECT COUNT(*) FROM programmes').fetchone()[0]
    if input_programmes != kept + merged + quarantined + alternatives_count:
        raise ValueError('programme conservation accounting failed')
    report = {'region': region, 'input_programmes': input_programmes, 'quarantined': quarantined,
              'unique_retained_programmes': kept, 'metadata_merged_records': merged,
              'preserved_alternative_records': alternatives_count,
              'identity_evidence': evidence, 'conflicts': conflicts,
              'mapping': [{'source_file': f, 'source_id': cid, 'canonical_id': canonical[n],
                           'owner': destinations[n][0]} for (f,cid),n in sorted(plan.items())]}
    (output / 'reconciliation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    counts = {}
    for label, stem in FILES[region].items():
        selected = sorted((n for n,t in destinations.items() if label in t), key=lambda n: canonical[n])
        if not selected:
            continue
        xml_path = output / stem.removesuffix('.gz')
        programmes = 0
        with xml_path.open('wb') as stream:
            stream.write(b'<?xml version="1.0" encoding="utf-8"?>\n<tv generator-info-name="Kaimandura/epg-de canonical release">\n')
            for n in selected:
                stream.write(ET.tostring(channel_nodes[n], encoding='utf-8') + b'\n')
            for n in selected:
                for (payload,) in connection.execute('SELECT xml FROM programmes WHERE identity=? ORDER BY start,stop,title', (n,)):
                    stream.write(payload + b'\n')
                    programmes += 1
            stream.write(b'</tv>\n')
        import shutil
        with xml_path.open('rb') as source, (output/stem).open('wb') as raw:
            with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as target:
                shutil.copyfileobj(source,target)
        counts[label] = {'channels':len(selected), 'programmes':programmes}
    programme_counts = dict(connection.execute('SELECT identity,COUNT(*) FROM programmes GROUP BY identity'))
    connection.close()
    result = audit({label:output/FILES[region][label] for label in counts}, aliases)
    report['outputs'] = counts
    validate_file_set(counts,region)
    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream,'sha256').hexdigest()
    report['input_sha256'] = {name:digest(path) for name,path in paths.items()}
    report['output_sha256'] = {name:digest(output/FILES[region][name]) for name in counts}
    report['evidence_sha256'] = {name:digest(output/name) for name in ('quarantine.jsonl','schedule-alternatives.jsonl')}
    report['retired_empty_feeds'] = sorted(set(FILES[region])-set(counts))
    report['audit'] = result
    report['status'] = result['status']
    (output / 'reconciliation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if result['status'] != 'passed':
        raise ValueError(f'reconciled release failed audit: {result["errors"][:5]}, {result["identity_violations"][:3]}')
    if mapping:
        category_names = {'USA-MASTER.xml.gz':'main', 'USA-FAST.xml.gz':'fast', 'USA-LOCAL.xml.gz':'local', 'USA-SPORTS.xml.gz':'sports'}
        by_id = {cid:n for (_label,cid),n in plan.items()}
        with mapping.open(encoding='utf-8-sig',newline='') as stream:
            reader = csv.DictReader(stream)
            fields = list(reader.fieldnames)
            if 'canonical_xmltv_id' not in fields:
                fields.append('canonical_xmltv_id')
            rows = list(reader)
        with (output/'usa-channel-mapping.csv').open('w',encoding='utf-8',newline='') as stream:
            writer = csv.DictWriter(stream,fieldnames=fields,lineterminator='\n')
            writer.writeheader()
            for row in rows:
                n = by_id[row.get('canonical_xmltv_id') or row['xmltv_id']]
                row['canonical_xmltv_id'] = canonical[n]
                row['category'] = category_names[destinations[n][0]]
                row['programme_count'] = str(programme_counts[n])
                writer.writerow(row)
        with (output/'usa-coverage.csv').open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['category','channel_count','programme_count','gzip_bytes'],lineterminator='\n')
            writer.writeheader()
            for label,stats in counts.items():
                writer.writerow({'category':category_names[label],'channel_count':stats['channels'],'programme_count':stats['programmes'],'gzip_bytes':(output/FILES[region][label]).stat().st_size})
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--region', choices=FILES, required=True)
    parser.add_argument('--mapping', type=Path)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--baseline-mapping',type=Path)
    args = parser.parse_args()
    paths = {label:args.source/stem for label,stem in FILES[args.region].items() if (args.source/stem).exists()}
    report = reconcile(paths,args.output,args.region,args.mapping)
    if args.baseline:
        baseline_paths={label:args.baseline/stem for label,stem in FILES[args.region].items() if (args.baseline/stem).exists()}
        baseline_report=reconcile(baseline_paths,args.output/'baseline',args.region,args.baseline_mapping)
        compare_lkg(report,baseline_report,args.region)
        report['lkg']={'status':'passed','baseline_outputs':baseline_report['outputs']}
        (args.output/'reconciliation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'outputs':report['outputs'],'quarantined':report['quarantined']}))


def compare_lkg(current,baseline,region):
    ratios={'channels':.70 if region=='DE' else .65,'programmes':.60 if region=='DE' else .55}
    for label,previous in baseline['outputs'].items():
        actual=current['outputs'].get(label,{})
        for metric,ratio in ratios.items():
            if actual.get(metric,0)<previous[metric]*ratio:
                raise ValueError(f'LKG regression {label} {metric}: {actual.get(metric,0)} < {ratio} * {previous[metric]}')


if __name__ == '__main__':
    main()
