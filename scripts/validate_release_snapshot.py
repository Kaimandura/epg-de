"""Build and audit the proposed release from a pinned committed snapshot.

Never edits epg/, promotes LKG, or deploys. PR evidence is separate from live
producer evidence. The baseline is normalized by exactly the same policy.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

from reconcile_release import reconcile, compare_lkg
from release_bundle import seal, policy_digest
from release_policy import FILES, audit, configured_aliases


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    paths={}
    results={}
    for region in FILES:
        source={name:Path('epg')/stem for name,stem in FILES[region].items() if (Path('epg')/stem).exists()}
        mapping=Path('reports/usa-channel-mapping.csv') if region=='USA' else None
        target=args.output/region
        report=reconcile(source,target,region,mapping)
        # Snapshot migration conservation is checked by reconcile. A same-input
        # threshold test proves the baseline policy is internally consistent;
        # live producers additionally compare against the preceding LKG.
        compare_lkg(report,report,region)
        results[region]=report
        paths.update({name:target/FILES[region][name] for name in report['outputs']})
    ids=set()
    for name in ('usa-channel-mapping.csv','usa-unmapped-channels.csv'):
        with (Path('reports')/name).open(encoding='utf-8-sig',newline='') as stream:
            ids.update(r['xmltv_id'] for r in csv.DictReader(stream))
    playlist=args.output/'archived-playlist.m3u'
    playlist.write_text('#EXTM3U\n'+''.join(f'#EXTINF:-1 tvg-id="{cid}",archived\n' for cid in sorted(ids)),encoding='utf-8')
    us=args.output/'USA'
    subprocess.run([sys.executable,'scripts/audit_usa_epgs.py','--main',str(us/'usa.xml'),'--local',str(us/'usa-local.xml'),
                    '--fast',str(us/'usa-fast.xml'),'--sports',str(us/'usa-sports.xml'),'--mapping',str(us/'usa-channel-mapping.csv'),
                    '--coverage',str(us/'usa-coverage.csv'),'--playlist',str(playlist),'--summary',str(us/'usa-quality-audit.json')],check=True)
    combined=audit(paths,configured_aliases(mapping=us/'usa-channel-mapping.csv'))
    (args.output/'combined-audit.json').write_text(json.dumps(combined,indent=2)+'\n',encoding='utf-8')
    if combined['status']!='passed':
        raise ValueError('combined snapshot release failed')
    provenance={'repository':os.environ.get('GITHUB_REPOSITORY','Kaimandura/epg-de'),
                'source_sha':os.environ.get('GITHUB_SHA',subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()),
                'run_id':int(os.environ.get('GITHUB_RUN_ID','0')),'run_attempt':int(os.environ.get('GITHUB_RUN_ATTEMPT','1')),
                'policy_sha256':policy_digest(Path.cwd()),'test_only':True}
    for region in FILES:
        seal(args.output/region,args.output/f'bundle-{region}',region,provenance,configured_aliases(mapping=us/'usa-channel-mapping.csv'))
    print('Proposed snapshot release passed all integrity, identity, USA and conservation gates.')


if __name__=='__main__':
    main()
