# rag-docs-demo

Question answering over a folder of business PDFs (purchase orders, invoices,
some of them scans) with page-level citations and a measured retrieval quality.

Built to show how I approach RAG in production, not as a tutorial:

- **Plain Anthropic SDK**, no LangChain / LlamaIndex layer. Every prompt and
  every retrieved chunk is visible and testable.
- **Local open-source embeddings** inside the container. No keys besides
  Anthropic, documents never leave the deployment.
- **Hybrid retrieval**: pgvector + Postgres full-text in one database (Neon).
- **Citations with page numbers** via Claude document blocks with citations
  enabled; the answer says "not in the documents" when retrieval is empty.
- **Eval set**: golden questions, recall@k, share of answers grounded in
  citations, share of correct refusals. Results table lives in this README.
- Cost and latency of every request shown next to the answer.

Stack: Python 3.12, FastAPI, Anthropic SDK, pgvector on Neon, Azure Container
Apps (scale to zero). Single web page UI; the backend is channel-agnostic
(WhatsApp Business API, Slack or Telegram can be attached).

Status: planning, 2026-11-16. See `progress.md`.
