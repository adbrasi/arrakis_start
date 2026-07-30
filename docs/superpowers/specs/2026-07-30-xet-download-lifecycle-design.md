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

- `hf download` with `hf_xet` remains the first Hugging Face backend.
- The local disk watchdog may report progress and heartbeat information, but
  it must not terminate XET solely because staging bytes did not grow.
- HTTP fallback starts only after the XET subprocess exits unsuccessfully.
- The existing global no-completion timeout remains the hard safety boundary
  for a genuinely stuck batch.
- Cancellation remains an immediate terminal result and never starts fallback
  or retry.

### Queue Deduplication

- Normalize each requested model to `(destination directory, filename)` before
  scheduling workers.
- Identical destination and source entries collapse into one queue item.
- Two different sources targeting the same destination are a configuration
  conflict and must fail before downloads start.
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

- XET preparation without local byte growth is logged as an informational
  heartbeat, not a warning or error.
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
7. the complete existing test suite remains green.

## Non-Goals

- Changing model URLs or preset contents.
- Deleting custom-node clones during shutdown.
- Adding a second download implementation.
- Hiding real XET process failures or authentication errors.
