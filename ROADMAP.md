# DocSage roadmap

## Known gaps

- **Live model calls.** The Claude, Gemini and OpenAI adapters are covered by request-shape unit
  tests with mocked SDK clients. A run with real keys is the next important check.
- **Benchmarks.** The evaluation harness runs end to end, but the bundled sample corpus (19 chunks)
  is too small to separate configurations. It needs a real corpus, a golden set of 30+ questions
  and a model judge.
- **GraphRAG.** Entity extraction and graph expansion sit behind a flag and are not yet
  benchmarked.
- **Docker image.** CI builds it; it has not been built locally.

## Next

1. Run the sample collection and a real corpus with a Claude or Gemini key, and record a benchmark
   with a model judge.
2. Grow the golden set to 30+ questions per corpus and commit the results to `eval/results/`.
3. Decide on the vector store: stay on Chroma or move to Qdrant or pgvector for server-side
   hybrid search.
4. Add an OpenTelemetry exporter alongside the built-in per-query traces.
5. Add authentication beyond a single shared token for multi-team use.
6. Add an explicit abstain path so the agent says "not found" when evidence stays insufficient.
