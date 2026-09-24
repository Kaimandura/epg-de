#!/usr/bin/env python3
from __future__ import annotations
import argparse, gzip, json, re, shutil
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

def norm(v: str) -> str:
    v=(v or "").strip()
    v=re.sub(r"^DE\s*(?:-|:|\\|)\s*","",v,flags=re.I)
    return re.sub(r"[^a-z0-9]+"," ",v.casefold()).strip()

def names(ch):
    return [(n.text or "").strip() for n in ch.findall("display-name") if (n.text or "").strip()]

def write(root: ET.Element, path: Path):
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path,encoding="utf-8",xml_declaration=True)
    gz=path.with_suffix(path.suffix+".gz")
    with path.open("rb") as src, gz.open("wb") as raw:
        with gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=0) as out: shutil.copyfileobj(src,out)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--target",type=Path,required=True)
    ap.add_argument("--source",type=Path,required=True)
    ap.add_argument("--config",type=Path,required=True)
    args=ap.parse_args()
    root=ET.parse(args.target).getroot()
    with gzip.open(args.source,"rb") as h: source=ET.fromstring(h.read())
    cfg=json.loads(args.config.read_text(encoding="utf-8"))["channels"]
    source_channels={}
    for ch in source.findall("channel"):
        for name in names(ch):
            source_channels.setdefault(norm(name),ch)
    source_programmes={}
    for p in source.findall("programme"):
        source_programmes.setdefault(p.attrib.get("channel",""),[]).append(p)
    target_ids={c.attrib.get("id","") for c in root.findall("channel")}
    active={p.attrib.get("channel","") for p in root.findall("programme")}
    added=[]
    missing=[]
    for row in cfg:
        tid=row["xmltv_id"]
        if tid in active:
            continue
        src=None
        for alias in row["source_names"]:
            src=source_channels.get(norm(alias))
            if src is not None: break
        if src is None:
            missing.append(row["name"]+":channel")
            continue
        sid=src.attrib.get("id","")
        progs=source_programmes.get(sid,[])
        if not progs:
            missing.append(row["name"]+":programmes")
            continue
        for c in list(root.findall("channel")):
            if c.attrib.get("id","")==tid: root.remove(c)
        ch=deepcopy(src); ch.attrib["id"]=tid
        for d in list(ch.findall("display-name")): ch.remove(d)
        d=ET.Element("display-name",{"lang":"de"}); d.text=row["name"]; ch.append(d)
        a=ET.Element("display-name",{"lang":"de"}); a.text="DE - "+row["name"]; ch.append(a)
        root.insert(len(root.findall("channel")),ch)
        for p in progs:
            q=deepcopy(p); q.attrib["channel"]=tid; root.append(q)
        added.append((tid,len(progs)))
    write(root,args.target)
    print("Magenta sports fallback added:",added)
    if missing:
        raise SystemExit("Required Magenta sports fallback missing: "+", ".join(missing))
    return 0
if __name__=="__main__": raise SystemExit(main())
