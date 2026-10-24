# ExecutionTrace snapshot lifecycle

`ExecutionTrace.make_snapshot()` is a read operation. It creates an independent
view of current trace facts and derives billing afresh from source billing items;
it does not append items, change timings, persist data, mirror data, or settle a
treasury charge.

`finalize()` creates one terminal snapshot using a single final timing cutoff.
The snapshot is validated before the trace becomes final. Later `finalize()` and
`make_snapshot()` calls return independent copies of that frozen result. Trace
mutation APIs reject writes after finalization.

If no work occurs after a snapshot, snapshot and final billing payloads are
identical. Persistence, mirroring, and settlement remain explicit operations
owned by their callers.
