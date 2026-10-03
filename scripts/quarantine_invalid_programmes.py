"""Preserve malformed upstream programme records outside the product release.

Never changes source files or valid programme metadata. Structural errors and
unknown references remain errors in the subsequent XMLTV gate.
"""
import argparse
import gzip
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from release_policy import timestamp


def sanitize(source, output):
    if output.exists():
        raise ValueError('output must not exist')
    output.parent.mkdir(parents=True,exist_ok=True)
    total=rejected=0
    rejection_path=output.with_suffix('.rejected.jsonl')
    with source.open('rb') as stream, output.open('wb') as target, rejection_path.open('w',encoding='utf-8') as evidence:
        iterator=ET.iterparse(stream,events=('start','end'))
        _,root=next(iterator)
        if root.tag!='tv':
            raise ValueError('expected XMLTV tv root')
        header=ET.tostring(ET.Element('tv',root.attrib),encoding='utf-8').removesuffix(b' />')
        target.write(b'<?xml version="1.0" encoding="utf-8"?>\n'+header+b'>\n')
        for event,node in iterator:
            if event!='end' or node.tag not in ('channel','programme'):
                continue
            reasons=[]
            if node.tag=='programme':
                total+=1
                if not any((n.text or '').strip() for n in node.findall('title')):
                    reasons.append('missing programme title')
                try:
                    start=timestamp(node.get('start',''))
                    stop=timestamp(node.get('stop')) if node.get('stop') else None
                    if stop is not None and stop<=start:
                        reasons.append('non-positive duration')
                except ValueError as exc:
                    reasons.append(str(exc))
            if reasons:
                rejected+=1
                evidence.write(json.dumps({'source_file':str(source),'reasons':reasons,
                                           'source_xml':ET.tostring(node,encoding='unicode')},ensure_ascii=False)+'\n')
            else:
                target.write(ET.tostring(node,encoding='utf-8')+b'\n')
            root.remove(node)
        target.write(b'</tv>\n')
    with output.open('rb') as inp, output.with_suffix('.xml.gz').open('wb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as zipped:
            shutil.copyfileobj(inp,zipped)
    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream,'sha256').hexdigest()
    report={'input_programmes':total,'retained_programmes':total-rejected,'quarantined_programmes':rejected,
            'input_sha256':digest(source),'output_sha256':digest(output),'evidence_sha256':digest(rejection_path),
            'status':'passed' if total and rejected/total<=.01 else 'failed'}
    output.with_suffix('.quarantine.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    if report['status']!='passed':
        raise ValueError('source corruption exceeds 1% or source is empty')
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(sanitize(args.source,args.output)))


if __name__=='__main__':
    main()
