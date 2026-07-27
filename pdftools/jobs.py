"""Per-request working directories and result registration.

Outputs are addressed by integer index, never by a client-supplied path, so no
download URL can reach outside its job directory. Cleanup is opportunistic:
every new job sweeps expired ones, which is enough for a local single-user tool
and avoids a background thread.
"""
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_TTL_SECONDS = 3600
_BASE_PREFIX = "pdftoolbox-"


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
        archive = self.root / "results.zip"
        used = set()
        with zipfile.ZipFile(str(archive), "w", zipfile.ZIP_DEFLATED) as zipped:
            for entry in self._outputs:
                name = entry["display_name"]
                if name in used:
                    stem = Path(name).stem
                    suffix = Path(name).suffix
                    counter = 2
                    while "{0}_{1}{2}".format(stem, counter, suffix) in used:
                        counter += 1
                    name = "{0}_{1}{2}".format(stem, counter, suffix)
                used.add(name)
                zipped.write(str(entry["path"]), arcname=name)
        return archive


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
        """Delete job directories older than the TTL. Returns how many went."""
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
        for path in self.base_dir.iterdir():
            if path.is_dir() and path.name not in self._jobs:
                try:
                    if path.stat().st_mtime < cutoff:
                        shutil.rmtree(str(path), ignore_errors=True)
                except OSError:
                    continue
        return removed
