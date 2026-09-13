"""Per-request working directories and result registration.

Outputs are addressed by integer index, never by a client-supplied path, so no
download URL can reach outside its job directory. Cleanup is opportunistic:
every new job sweeps expired ones, which is enough for a local single-user tool
and avoids a background thread.
"""

import os
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_TTL_SECONDS = 3600
_BASE_PREFIX = "pdftoolbox-"


def safe_display_name(display_name: str) -> str:
    """Reduce a display name to a bare filename, safe to hand to a client.

    Display names are meant to echo a client-supplied upload filename and must
    never be trusted as a path: zip_outputs presents a flat namespace, so any
    directory separators or ".." segments are stripped down to the final path
    component here, at the point the untrusted archive is built, rather than
    relying on the caller to have sanitized it first. The same normalization
    serves a ``Content-Disposition`` filename on a download, which is the same
    untrusted value going out through a different channel.
    """
    candidate = Path(display_name.replace("\\", "/")).name
    if candidate in ("", ".", ".."):
        candidate = "file"
    return candidate


def zip_files(archive, members: List[Tuple[str, Path]]) -> Path:
    """Zip ``members``, given as (display name, path) pairs, into ``archive``.

    Display names are untrusted -- they echo what a client called an upload --
    so each is reduced to a bare filename, and names that collide once reduced
    are made distinct rather than overwriting one another. A zip whose second
    member silently replaced the first would lose a page the user asked for.
    """
    archive = Path(archive)
    used = set()
    with zipfile.ZipFile(str(archive), "w", zipfile.ZIP_DEFLATED) as zipped:
        for display_name, path in members:
            name = safe_display_name(display_name)
            if name in used:
                stem = Path(name).stem
                suffix = Path(name).suffix
                counter = 2
                while "{0}_{1}{2}".format(stem, counter, suffix) in used:
                    counter += 1
                name = "{0}_{1}{2}".format(stem, counter, suffix)
            used.add(name)
            zipped.write(str(path), arcname=name)
    return archive


class Job:
    """One request's inputs, outputs, and previews."""

    def __init__(self, job_id: str, root: Path):
        self.id = job_id
        self.root = Path(root)
        self.inputs = self.root / "inputs"
        self.outputs = self.root / "outputs"
        self.previews = self.root / "previews"
        for directory in (self.inputs, self.outputs, self.previews):
            directory.mkdir(parents=True, exist_ok=True)
        self._outputs: List[Dict] = []
        # Scratch space for whichever route owns this job, the way each
        # output already carries its own meta. The splitter keeps the
        # uploaded document and the page selection here, because its work
        # spans several requests rather than one.
        self.meta: Dict = {}

    def touch(self) -> None:
        """Mark the job as still in use, so the TTL sweep spares it.

        Expiry is measured from the directory's mtime, which only moves when
        something is written. A job whose pages are merely being looked at
        writes nothing, so a long editing session would age into a sweep and
        take the user's document with it mid-edit.
        """
        os.utime(str(self.root), None)

    def add_output(self, path, display_name: str, meta: Optional[Dict] = None) -> int:
        """Register a finished file and return its index."""
        self._outputs.append(
            {
                "path": Path(path),
                "display_name": display_name,
                "meta": meta or {},
            }
        )
        return len(self._outputs) - 1

    def output(self, index: int) -> Dict:
        if index < 0 or index >= len(self._outputs):
            raise IndexError("No output {0} in job {1}".format(index, self.id))
        return self._outputs[index]

    def outputs_list(self) -> List[Dict]:
        return list(self._outputs)

    def zip_outputs(self) -> Path:
        return zip_files(
            self.root / "results.zip",
            [(entry["display_name"], entry["path"]) for entry in self._outputs],
        )


class JobStore:
    """Creates jobs under a base directory and expires old ones."""

    def __init__(self, base_dir=None, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        if base_dir is None:
            base_dir = Path(tempfile.gettempdir()) / (_BASE_PREFIX + "jobs")
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self._jobs: Dict[str, Job] = {}

    def create(self) -> Job:
        self.cleanup_expired()
        job_id = uuid.uuid4().hex
        job = Job(job_id, self.base_dir / job_id)
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job:
        return self._jobs[job_id]

    def cleanup_expired(self) -> int:
        """Delete directories older than the TTL: tracked jobs and orphans alike.

        Returns the total number of directories removed, including both jobs
        still in the registry and directories left behind by a previous
        process run that this instance never registered.
        """
        cutoff = time.time() - self.ttl_seconds
        removed = 0
        for job_id, job in list(self._jobs.items()):
            try:
                modified = job.root.stat().st_mtime
            except OSError:
                self._jobs.pop(job_id, None)
                continue
            if modified < cutoff:
                shutil.rmtree(str(job.root), ignore_errors=True)
                self._jobs.pop(job_id, None)
                removed += 1
        # Directories left behind by a previous process run.
        try:
            leftovers = list(self.base_dir.iterdir())
        except OSError:
            # macOS periodically sweeps $TMPDIR, so the base directory can
            # vanish under a long-running process. Unguarded, that turns
            # every later request into a 500 from in here; recreating it
            # leaves the store usable and there is nothing left to expire.
            self.base_dir.mkdir(parents=True, exist_ok=True)
            return removed
        for path in leftovers:
            if path.is_dir() and path.name not in self._jobs:
                try:
                    if path.stat().st_mtime < cutoff:
                        shutil.rmtree(str(path), ignore_errors=True)
                        removed += 1
                except OSError:
                    continue
        return removed
