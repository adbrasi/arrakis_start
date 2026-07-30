# XET Download Lifecycle Design

## Goal

Keep Hugging Face XET as the primary fast download path, make cancellation
truthful, remove duplicate work, and give shutdown an explicit destructive
contract for incomplete model data.

## Confirmed Problems

1. The XET process is terminated after 120 seconds without visible growth in
   Arrakis' local staging path. XET does not guarantee that this path grows
   continuously, so local disk silence is not a valid backend failure signal.
2. Downloads from multiple selected presets are filtered before installation
   but not deduplicated. The reported run contained 58 entries for 47 unique
   destination paths.
3. A user cancellation can be logged as a retryable download failure and the
   final summary reports normal completion counts for cancelled work.
4. Shutdown uses the same resume-preserving cancellation behavior as the
   Cancel button, although the requested shutdown contract is to remove
   incomplete model data.

## Download Contract

### XET

- A killable `huggingface_hub` subprocess with `hf_xet` remains the first
  Hugging Face backend.
- The worker forwards the official XET transfer and reconstruction callbacks
  as structured events, so Arrakis can report bytes, percentage, and speed even
  when subprocess output is piped.
- A callback `TypeError`/`AttributeError` triggers a compatibility retry without
  the callback, reusing the same XET partial cache. It does not disable XET.
- The local disk watchdog may report heartbeat information, but it must not
  terminate XET solely because staging bytes did not grow, and it must not
  publish progress for XET at all: XET owns progress reporting on its own path,
  and a second disk-derived stream disagrees with it by construction.
- XET liveness is judged on *delivered bytes*, taken from the worker's own
  progress events rather than from disk. A healthy transfer legitimately starts
  at tens of KB/s before accelerating, so a plain rate threshold is not a valid
  failure signal. Two rules apply, both env-tunable and both deliberately
  generous enough that a normal slow start never reaches them:
  - delivered bytes stop growing entirely for `XET_NO_PROGRESS_SECONDS`
    (default 240) — the transfer is dead, terminate and fall back;
  - after `XET_RATE_GRACE_SECONDS` (default 600), the average delivered rate is
    still below `XET_MIN_BYTES_PER_SEC` (default 100 KB/s) — the transfer is
    crawling long past any warm-up, terminate and fall back.
- HTTP fallback starts after the XET subprocess exits unsuccessfully, including
  when the liveness rules above terminated it.
- The existing global no-completion timeout remains the hard safety boundary
  for a genuinely stuck batch. It is a last resort, not the primary guard, and
  it is reported as its own outcome — never as a user cancellation.
- Cancellation remains an immediate terminal result and never starts fallback
  or retry.

### Queue Deduplication

- Normalize each requested model to `(destination directory, filename)` before
  scheduling workers.
- Identical destination and source entries collapse into one queue item.
- The destination name is derived the same way the downloader derives it, so an
  entry with an empty `filename` participates in the same destination check as a
  named one.
- Two different sources targeting the same destination are a configuration
  conflict. It is reported as a failure and the first source wins, but it must
  never cancel the unrelated downloads queued alongside it: aborting the batch
  produced zero downloads with zero recorded failures, which the installer then
  reported as a successful install.
- Progress totals use the deduplicated queue size.

## Cancellation and Shutdown

The two UI actions have intentionally different contracts:

- **Cancel installation:** stop downloads, clones, and package installation;
  preserve completed files and resumable partial model data.
- **Shutdown:** request cancellation, wait for the active installer to unwind,
  remove incomplete model data, preserve completed final models, stop ComfyUI,
  and terminate Arrakis Start.

Incomplete model cleanup includes:

- the private Hugging Face partial root (`.arrakis-hf-partials`);
- generic `*.arrakis.part` payloads;
- their `*.aria2` control files;
- legacy model-adjacent `.aria2` partial pairs when the final model is known to
  be incomplete.

Cleanup never removes a completed final model and never removes custom-node
repositories.

The shutdown confirmation text must state that incomplete model downloads will
be deleted. Shutdown waits for cancellation to reach a terminal state before
cleanup, avoiding deletion while a worker can still write.

## Logging Contract

- XET network and reconstruction callbacks are reported separately as
  `[XET/rede]` and `[XET/arquivo]`.
- XET preparation without local byte growth is logged as an informational
  heartbeat, not a warning or error.
- Custom-node `uv`/`pip` output is forwarded without discarding resolver and
  installation phases. Silent heartbeats report CPU, I/O, RAM, and process
  count, or explicitly state that no activity is detectable.
- `cancelled_by_user` is terminal and produces no retry message.
- Cancelled queued work is not counted as a concluded download.
- Cancellation receives its own summary, separate from success/failure.
- The initial count reports both raw and unique entries when duplicates were
  removed.

## Tests

Automated tests will prove:

1. local staging silence does not terminate a live XET subprocess;
2. a real non-zero XET exit triggers HTTP fallback;
3. duplicate destinations collapse and conflicting destinations fail;
4. cancellation does not retry or record a normal download failure;
5. Cancel preserves partials;
6. Shutdown deletes partials only after cancellation and preserves completed
   final models;
7. structured XET callbacks are parsed into network/reconstruction progress;
8. pip phases and silent-process telemetry remain visible;
9. the complete existing test suite remains green.

## Non-Goals

- Changing model URLs or preset contents.
- Deleting custom-node clones during shutdown.
- Adding a second download implementation.
- Hiding real XET process failures or authentication errors.
