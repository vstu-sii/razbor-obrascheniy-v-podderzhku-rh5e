"""Code-controlled cheap fallback; the model cannot choose its own budget policy."""
def analyze(engine, request, mode, timeout):
    if mode == 'extractive' and getattr(engine,'mode',None) == 'llm':
        # Do not mutate a shared engine used by concurrent jobs.
        cheap = type(engine)(engine.kb, engine.schema, engine.gate, mode='extractive',
                             trace_path=engine.trace_path, prompt_version=engine.prompt_version)
        return cheap.analyze(request, max_attempts=1, deadline_seconds=timeout)
    return engine.analyze(request, max_attempts=1, deadline_seconds=timeout)
