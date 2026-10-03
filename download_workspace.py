"""Download a captured Suno workspace via direct HTTP, without browser downloads."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError

# Keep imported local modules from creating files beside the source code.
sys.dont_write_bytecode = True

import av
from direct_audio import request, resolve_media, playback_decryptor, RecordingReader

DOWNLOADS_ROOT = Path(__file__).resolve().parent / "downloads"


def downloads_path(path):
    """Reject paths outside the private, Git-ignored downloads directory."""
    resolved = path.resolve()
    if not resolved.is_relative_to(DOWNLOADS_ROOT.resolve()):
        raise ValueError("Manifest and output paths must be inside downloads/")
    return resolved


def load_manifest(path):
    text = path.read_text(encoding="utf-8-sig")
    expected = re.search(r"^# expected_song_count=(\d+)$", text, re.M)
    songs, ids = [], set()
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        title, *row_ids = line.split("|")
        if not title or not row_ids:
            raise ValueError("Invalid manifest row")
        for song_id in row_ids:
            if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", song_id):
                raise ValueError("Invalid song ID")
            if song_id in ids:
                raise ValueError("Duplicate song ID in manifest")
            ids.add(song_id)
            songs.append({"id": song_id, "title": title, "url": f"https://suno.com/song/{song_id}"})
    if not expected or len(songs) != int(expected[1]):
        raise ValueError("Manifest count does not match the captured workspace count")
    return songs


def safe_filename(title, song_id, extension=".m4a"):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:95] or "Untitled"
    return f"{title}_{song_id}{extension}"


def file_hash(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def is_verified_file(path, report):
    return (path.is_file() and path.stat().st_size == report.get("bytes")
            and file_hash(path) == report.get("sha256"))


def save_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def download_once(song, root):
    report_path = root / "_metadata" / (song["id"] + ".json")
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        output = root / report["file"]
        if output.parent.resolve() != root.resolve():
            raise ValueError("Invalid output path in saved report")
        if report.get("id") == song["id"] and is_verified_file(output, report):
            return {**report, "status": "skipped_verified"}
        raise RuntimeError("Existing file failed verification; preserved for inspection")
    media = resolve_media(song["url"])
    extension = {"m4a-opus": ".m4a", "webm-opus": ".webm", "mp3": ".mp3"}.get(media.get("content_type"))
    if extension is None:
        raise ValueError("Unsupported media format")
    output = root / safe_filename(song["title"], song["id"], extension)
    if output.exists():
        raise FileExistsError("Existing file has no verification report; preserved")
    decryptor = playback_decryptor(song["url"], media)
    partial = output.with_name(output.name + ".part")
    try:
        with request(media["url"]) as response, partial.open("wb") as target:
            expected = int(response.headers.get("Content-Length", 0))
            etag = response.headers.get("ETag", "").strip('"')
            reader = RecordingReader(response, target, decryptor)
            while reader.read(262144):
                pass
            if decryptor is not None and decryptor.finalize():
                raise RuntimeError("Unexpected trailing plaintext")
            if expected and reader.count != expected:
                raise RuntimeError("Incomplete HTTP response")
            etag_matches = reader.md5.hexdigest() == etag if re.fullmatch(r"[0-9a-f]{32}", etag) else None
            if etag_matches is False:
                raise RuntimeError("HTTP body does not match server checksum")
        with av.open(str(partial)) as container:
            stream = container.streams.audio[0]
            codec = stream.codec_context.name
            rate = stream.codec_context.sample_rate
            channels = stream.codec_context.channels
            samples = sum(frame.samples for frame in container.decode(stream))
        if not samples or not rate:
            raise RuntimeError("No audio decoded")
        report = {**song, "file": output.name, "bytes": partial.stat().st_size,
                  "sha256": reader.sha256.hexdigest(), "wire_sha256": reader.wire_sha256.hexdigest(),
                  "expected_bytes": expected, "wire_etag_md5_matches": etag_matches,
                  "codec": codec, "sample_rate": rate, "channels": channels,
                  "duration_seconds": round(samples / rate, 3), "whole_file_decode": "passed",
                  "authentication": "server-granted anonymous playback" if decryptor else "none",
                  "completed_at": datetime.now(timezone.utc).isoformat(), "status": "downloaded"}
        if not is_verified_file(partial, report):
            raise RuntimeError("Saved file checksum mismatch")
        partial.rename(output)
        save_json(report_path, report)
        return report
    finally:
        # Only the partial file owned by this individual download is removed.
        partial.unlink(missing_ok=True)


def download_song(song, root):
    for attempt in range(3):
        try:
            return download_once(song, root)
        except Exception as exc:
            transient = (isinstance(exc, HTTPError) and (exc.code == 429 or exc.code >= 500)
                         or isinstance(exc, (TimeoutError, URLError)) and not isinstance(exc, HTTPError))
            if transient and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            return {**song, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="Workspace list inside downloads/ (title|song ID per row)")
    parser.add_argument("--output", type=Path,
                        help="Destination inside downloads/; default: downloads/<manifest name>")
    parser.add_argument("--workers", type=int, choices=range(1, 7), default=3)
    parser.add_argument("--limit", type=int, help="Process only the first N songs for an initial check")
    args = parser.parse_args()
    try:
        manifest = downloads_path(args.manifest)
        workspace_name = manifest.stem.removeprefix("workspace_") or "workspace"
        root = downloads_path(args.output if args.output is not None
                              else DOWNLOADS_ROOT / workspace_name)
        songs = load_manifest(manifest)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    total = len(songs)
    if args.limit is not None:
        if args.limit <= 0:
            parser.error("--limit must be positive")
        songs = songs[:args.limit]
    (root / "_metadata").mkdir(parents=True, exist_ok=True)
    results = {}
    started = time.monotonic()
    def checkpoint():
        summary = {"workspace": root.name, "manifest_count": total, "requested": len(songs),
                   "completed": len(results), "downloaded": sum(r["status"] == "downloaded" for r in results.values()),
                   "skipped_verified": sum(r["status"] == "skipped_verified" for r in results.values()),
                   "failed": sum(r["status"] == "failed" for r in results.values()),
                   "elapsed_seconds": round(time.monotonic() - started, 1),
                   "results": [results[s["id"]] for s in songs if s["id"] in results]}
        save_json(root / "download_report.json", summary)
        return summary
    print(f"Starting {len(songs)} songs; direct HTTP; {args.workers} workers", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_song, song, root): song for song in songs}
        for future in as_completed(futures):
            result = future.result()
            results[result["id"]] = result
            checkpoint()
            print(f"[{len(results)}/{len(songs)}] {result['status']} {result['title']} {result['id']}", flush=True)
            if result["status"] == "failed":
                print(result["error"], flush=True)
    summary = checkpoint()
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False), flush=True)
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
