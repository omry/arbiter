Keep artifact lookup, bookkeeping, and streaming file I/O off the HTTP event
loop under bounded admission. Downloads hold capacity through backpressure
and file closure while preserving HEAD and one-time GET behavior.
