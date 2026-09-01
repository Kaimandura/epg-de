#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BatchTask:
    round_number: int
    site: str
    batch_number: int
    selections: dict[str, dict[str, Any]]


@dataclass
class BatchResult:
    returncode: int
    timed_out: bool
    channels: dict[str, ET.Element]
    programmes: dict[str, list[ET.Element]]
    log_tail: str


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return slug or "unknown-site"


def chunked(items: list[tuple[str, dict[str, Any]]], size: int) -> list[list[tuple[str, dict[str, Any]]]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def write_channels_file(path: Path, selections: dict[str, dict[str, Any]]) -> None:
    root = ET.Element("channels")
    for xmltv_id in sorted(selections):
        candidate = selections[xmltv_id]
        node = ET.SubElement(root, "channel", candidate["attrs"])
        node.text = candidate["name"]
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass


def run_grab(
    epg_root: Path,
    channels: Path,
    output: Path,
    days: int,
    timeout_seconds: int,
) -> tuple[int, bool, str]:
    cmd = [
        "npm",
        "run",
        "grab",
        "---",
        f"--channels={channels.resolve()}",
        f"--output={output.resolve()}",
        f"--days={days}",
        "--timeout=30000",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=epg_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=(os.name != "nt"),
    )
    timed_out = False
    try:
        stdout, _ = proc.communicate(timeout=max(1, timeout_seconds))
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_process(proc)
        stdout, _ = proc.communicate()
    lines = stdout.splitlines()
    return proc.returncode if proc.returncode is not None else 124, timed_out, "\n".join(lines[-20:])


def parse_guide(path: Path) -> tuple[dict[str, ET.Element], dict[str, list[ET.Element]]]:
    channels: dict[str, ET.Element] = {}
    programmes: dict[str, list[ET.Element]] = defaultdict(list)
    if not path.exists() or path.stat().st_size == 0:
        return channels, programmes
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return channels, programmes
    for channel in root.findall("channel"):
        channel_id = channel.attrib.get("id", "")
        if channel_id:
            channels[channel_id] = deepcopy(channel)
    for programme in root.findall("programme"):
        channel_id = programme.attrib.get("channel", "")
        if channel_id:
            programmes[channel_id].append(deepcopy(programme))
    return channels, programmes


def run_batch(
    task: BatchTask,
    epg_root: Path,
    tmp_path: Path,
    days: int,
    timeout_seconds: int,
) -> BatchResult:
    slug = safe_slug(task.site)
    prefix = f"r{task.round_number:02d}-{slug}-{task.batch_number:03d}"
    channels_path = tmp_path / f"{prefix}.channels.xml"
    guide_path = tmp_path / f"{prefix}.guide.xml"
    write_channels_file(channels_path, task.selections)
    returncode, timed_out, log_tail = run_grab(
        epg_root=epg_root,
        channels=channels_path,
        output=guide_path,
        days=days,
        timeout_seconds=timeout_seconds,
    )
    channels, programmes = parse_guide(guide_path)
    return BatchResult(
        returncode=returncode,
        timed_out=timed_out,
        channels=channels,
        programmes=programmes,
        log_tail=log_tail,
    )


def programme_signature(programme: ET.Element) -> tuple[str, str, str, str]:
    return (
        programme.attrib.get("channel", ""),
        programme.attrib.get("start", ""),
        programme.attrib.get("stop", ""),
        programme.findtext("title") or "",
    )


def write_output(
    output: Path,
    candidate_data: dict[str, list[dict[str, Any]]],
    best_channels: dict[str, ET.Element],
    best_programmes: dict[str, list[ET.Element]],
) -> None:
    root = ET.Element("tv", {"generator-info-name": "Kaimandura/epg-de"})

    for xmltv_id in sorted(candidate_data):
        channel = best_channels.get(xmltv_id)
        if channel is None:
            candidate = candidate_data[xmltv_id][0]
            channel = ET.Element("channel", {"id": xmltv_id})
            display = ET.SubElement(channel, "display-name")
            display.text = candidate["name"]
        else:
            channel = deepcopy(channel)
            channel.attrib["id"] = xmltv_id
        root.append(channel)

    seen: set[tuple[str, str, str, str]] = set()
    all_programmes: list[ET.Element] = []
    for xmltv_id in sorted(best_programmes):
        for source_programme in best_programmes[xmltv_id]:
            programme = deepcopy(source_programme)
            programme.attrib["channel"] = xmltv_id
            signature = programme_signature(programme)
            if signature in seen:
                continue
            seen.add(signature)
            all_programmes.append(programme)

    all_programmes.sort(
        key=lambda item: (
            item.attrib.get("channel", ""),
            item.attrib.get("start", ""),
            item.attrib.get("stop", ""),
            item.findtext("title") or "",
        )
    )
    for programme in all_programmes:
        root.append(programme)

    output.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output, encoding="utf-8", xml_declaration=True)

    gzip_path = output.with_suffix(output.suffix + ".gz")
    with output.open("rb") as source, gzip_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            shutil.copyfileobj(source, target)


def write_coverage(
    path: Path,
    candidate_data: dict[str, list[dict[str, Any]]],
    best_programmes: dict[str, list[ET.Element]],
    best_source: dict[str, dict[str, Any]],
    attempts: dict[str, list[str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "xmltv_id",
                "name",
                "status",
                "programme_count",
                "selected_site",
                "candidate_count",
                "attempt_count",
                "attempts",
                "reasons",
            ]
        )
        for xmltv_id in sorted(candidate_data):
            candidates = candidate_data[xmltv_id]
            programmes = best_programmes.get(xmltv_id, [])
            selected = best_source.get(xmltv_id)
            reasons = sorted(
                {reason for item in candidates for reason in item.get("reasons", [])}
            )
            attempt_list = attempts.get(xmltv_id, [])
            writer.writerow(
                [
                    xmltv_id,
                    candidates[0]["name"],
                    "OK" if programmes else "NO_EPG",
                    len(programmes),
                    selected["attrs"].get("site", "") if selected else "",
                    len(candidates),
                    len(attempt_list),
                    ";".join(attempt_list),
                    ";".join(reasons),
                ]
            )


def build_batches(
    selections: dict[str, dict[str, Any]],
    round_number: int,
    batch_size: int,
) -> dict[str, deque[BatchTask]]:
    by_site: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for xmltv_id, candidate in selections.items():
        site = candidate["attrs"].get("site", "").strip() or "unknown-site"
        by_site[site].append((xmltv_id, candidate))

    queues: dict[str, deque[BatchTask]] = {}
    for site in sorted(by_site):
        items = sorted(by_site[site], key=lambda item: item[0])
        queues[site] = deque(
            BatchTask(
                round_number=round_number,
                site=site,
                batch_number=batch_number,
                selections=dict(batch),
            )
            for batch_number, batch in enumerate(chunked(items, batch_size), start=1)
        )
    return queues


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a deduplicated Germany XMLTV guide with bounded parallel source fallback."
    )
    parser.add_argument("--epg-root", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--min-programmes", type=int, default=4)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--grab-timeout-seconds", type=int, default=180)
    parser.add_argument("--overall-timeout-seconds", type=int, default=5400)
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.batch_size < 1:
        parser.error("--batch-size must be >= 1")
    if args.grab_timeout_seconds < 1:
        parser.error("--grab-timeout-seconds must be >= 1")
    if args.overall_timeout_seconds < 1:
        parser.error("--overall-timeout-seconds must be >= 1")

    payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    candidate_data: dict[str, list[dict[str, Any]]] = payload["channels"]

    best_channels: dict[str, ET.Element] = {}
    best_programmes: dict[str, list[ET.Element]] = {}
    best_source: dict[str, dict[str, Any]] = {}
    attempts: dict[str, list[str]] = defaultdict(list)
    next_index = {xmltv_id: 0 for xmltv_id in candidate_data}
    pending = set(candidate_data)

    started = time.monotonic()
    deadline = started + args.overall_timeout_seconds
    overall_timed_out = False

    with tempfile.TemporaryDirectory(prefix="epg-de-") as tmp:
        tmp_path = Path(tmp)

        for round_number in range(1, args.rounds + 1):
            if time.monotonic() >= deadline:
                overall_timed_out = True
                break

            selections: dict[str, dict[str, Any]] = {}
            for xmltv_id in sorted(pending):
                index = next_index[xmltv_id]
                candidates = candidate_data[xmltv_id]
                if index < len(candidates):
                    selections[xmltv_id] = candidates[index]

            if not selections:
                break

            queues = build_batches(selections, round_number, args.batch_size)
            active_sites: set[str] = set()
            futures: dict[Future[BatchResult], BatchTask] = {}
            resolved_this_round: set[str] = set()
            attempted_this_round = 0

            def submit_available(executor: ThreadPoolExecutor) -> None:
                nonlocal overall_timed_out
                while len(futures) < args.workers:
                    if time.monotonic() >= deadline:
                        overall_timed_out = True
                        return
                    submitted = False
                    for site in sorted(queues):
                        queue = queues[site]
                        if not queue or site in active_sites:
                            continue
                        task = queue.popleft()
                        if not queue:
                            del queues[site]
                        active_sites.add(site)
                        remaining = max(1, int(deadline - time.monotonic()))
                        batch_timeout = min(args.grab_timeout_seconds, remaining)
                        future = executor.submit(
                            run_batch,
                            task,
                            args.epg_root,
                            tmp_path,
                            args.days,
                            batch_timeout,
                        )
                        futures[future] = task
                        submitted = True
                        break
                    if not submitted:
                        return

            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                submit_available(executor)
                while futures:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        overall_timed_out = True
                        break

                    done, _ = wait(
                        futures,
                        timeout=min(30.0, remaining),
                        return_when=FIRST_COMPLETED,
                    )
                    if not done:
                        continue

                    for future in done:
                        task = futures.pop(future)
                        active_sites.discard(task.site)
                        attempted_this_round += len(task.selections)

                        try:
                            result = future.result()
                        except Exception as exc:
                            result = BatchResult(
                                returncode=1,
                                timed_out=False,
                                channels={},
                                programmes=defaultdict(list),
                                log_tail=f"{type(exc).__name__}: {exc}",
                            )

                        if result.timed_out:
                            batch_status = "TIMEOUT"
                        elif result.returncode != 0:
                            batch_status = f"ERROR({result.returncode})"
                        else:
                            batch_status = "OK"

                        print(
                            f"Round {round_number} | {task.site} | "
                            f"batch {task.batch_number} | {len(task.selections)} channels | "
                            f"{batch_status}"
                        )
                        if batch_status != "OK" and result.log_tail:
                            print(result.log_tail)

                        for xmltv_id, candidate in task.selections.items():
                            programmes = result.programmes.get(xmltv_id, [])
                            attempts[xmltv_id].append(
                                f"{task.site}:{batch_status}:{len(programmes)}"
                            )
                            previous = best_programmes.get(xmltv_id, [])
                            if len(programmes) > len(previous):
                                best_programmes[xmltv_id] = programmes
                                if xmltv_id in result.channels:
                                    best_channels[xmltv_id] = result.channels[xmltv_id]
                                best_source[xmltv_id] = candidate

                            if len(best_programmes.get(xmltv_id, [])) >= args.min_programmes:
                                resolved_this_round.add(xmltv_id)
                            else:
                                next_index[xmltv_id] += 1
                                if next_index[xmltv_id] >= len(candidate_data[xmltv_id]):
                                    resolved_this_round.add(xmltv_id)

                    submit_available(executor)

                if overall_timed_out:
                    for future in futures:
                        future.cancel()

            pending -= resolved_this_round
            print(
                f"Round {round_number}: attempted {attempted_this_round}, "
                f"resolved {len(resolved_this_round)}, pending {len(pending)}"
            )

            if overall_timed_out:
                break

    write_output(args.output, candidate_data, best_channels, best_programmes)
    write_coverage(args.coverage, candidate_data, best_programmes, best_source, attempts)

    with_programmes = sum(1 for key in candidate_data if best_programmes.get(key))
    total_programmes = sum(len(items) for items in best_programmes.values())
    elapsed = time.monotonic() - started
    print(
        f"Built {args.output}: {with_programmes}/{len(candidate_data)} channels with EPG, "
        f"{total_programmes} programmes in {elapsed:.1f}s."
    )
    if overall_timed_out:
        print(
            "Warning: overall build deadline reached; remaining candidates were not attempted. "
            "Validation/baseline gates decide whether this candidate may be published."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
