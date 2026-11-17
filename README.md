# rag-docs-demo

Question answering over a folder of business PDFs (purchase orders and invoices, a third of
them scans) with page-level citations and a measured retrieval quality.

Built to show how I approach RAG for production, not as a tutorial:

- **Plain Anthropic SDK**, no LangChain / LlamaIndex layer. Every prompt and every retrieved
  chunk is visible on the page and covered by tests.
- **Local open-source embeddings** inside the container (`BAAI/bge-small-en-v1.5` on ONNX
  Runtime). No key besides Anthropic; documents never leave the deployment.
- **OCR for scans** in the process too (RapidOCR, ONNX): no cloud OCR, no system packages.
- **Hybrid retrieval in one Postgres**: pgvector for meaning, `tsvector` for exact document
  numbers and SKUs, fused with reciprocal rank fusion, in a single SQL query.
- **Citations with page numbers** through Claude's document blocks; the answer says
  "not in the documents" when retrieval has nothing, and the eval checks that it does.
- **Eval set with exact answers**: 103 golden questions derived from the corpus ground truth,
  retrieval recall@k, answer accuracy, refusal behaviour, cost and latency. No model grades
  another model.
- **Cost and latency of every request** shown next to the answer; a daily spend ceiling and a
  per-client rate limit protect the public demo.

Stack: Python 3.12, FastAPI, Anthropic SDK (Claude Opus 5.5), fastembed, RapidOCR,
pypdfium2, Postgres 17 + pgvector (Neon in the cloud), Azure Container Apps (scale to zero).

## Measured quality

Retrieval, 93 answerable questions, k = chunks returned, a hit = the page that holds the
answer is among the top-k pages (full report: [`data/eval/latest-retrieval.md`](data/eval/latest-retrieval.md)):

| Metric | Value |
|---|---|
| page recall@1 | 87% |
| page recall@3 | 99% |
| page recall@5 | 99% |
| document recall@1 | 94% |
| page recall@3, text-layer pages (n=56) | 98% |
| page recall@3, scanned pages (n=37) | 100% |
| retrieval time, mean (embed + SQL) | 180 ms |

Answers, the same questions through Claude Opus 5.5 with the top-6 chunks (full report:
[`data/eval/latest-full.md`](data/eval/latest-full.md)). An answer is correct when it holds
the exact expected value: a number in either thousands convention, a date in any of its
formats, a name or an IBAN as written. A refusal is correct when the question has no answer
in the corpus.

| Metric | Value |
|---|---|
| answer accuracy, 93 answerable questions | 99% |
| accuracy on text-layer documents / on scans | 98% / 100% |
| correct refusals, 10 unanswerable questions | 100% |
| false refusals | 1% |
| answers with a citation on the gold page | 100% |
| model latency, mean / p90 | 3.0 s / 4.0 s |
| cost per question / whole run | $0.015 / $1.50 |
| input tokens per question, mean | 3 200 |

How the retrieval numbers were reached, in order: 91% → 97% after restoring the spaces
RapidOCR drops inside a line ("InvoiceHH-0107" is not "HH-0107" to a tokenizer); 97% → 100%
after fixing a `LIMIT` without `ORDER BY` in the lexical ranking and raising the
document-number boost above the ceiling of reciprocal rank fusion. The eval found all three.
The question set was then regenerated (see the first bullet below), and on that set the
ranking went 96% → 99% by counting the identifiers a chunk matches instead of flagging them.

Two full runs so far. The first scored 96% on answers, with four misses, all outside the
model, and the eval is what exposed them:

- Two questions asked "how many X" about a SKU that sat on three lines of the same order.
  The model listed all three and refused to pick one, which is the right behaviour. The
  question generator now asks only about SKUs that occur once in a document.
- On one scan the OCR detector dropped the IBAN line, sitting between two tight neighbours;
  the model correctly refused to borrow the IBAN from a sibling invoice. The detector now
  runs on the full 220 dpi render instead of a 2000 px reduction.
- One date came back as "23Jul 2025": the recognizer drops spaces, and this shape of glue
  was not covered. One more respacing rule.
- Reading the retrieved chunks for those questions showed a fourth defect the metrics had
  not flagged: on rotated scans the row grouping chained neighbouring table rows into one
  line, so a three-row table arrived as one row. The grouping now estimates the page skew
  from each box's nearest neighbour to the right and levels the boxes before grouping.

The second run, after those fixes, scored 99%. Its one miss is an OCR limit: the only three
pages that name "Cable 3G1.5 mm2, 100 m drum" are scans, the recognizer glues the end of
that line into "100mdrum", and no tokenizer turns that back into "100", "m" and "drum"; the
model refused rather than guess a vendor, which is the right call. The same run showed that
the identifier boost was a flag: a chunk naming both the order and the SKU the question
asked about lost to chunks naming only one of them with a better vector rank. The boost now
counts identifiers matched. The retrieval table above is measured after that change; the
answers table is from the run just before it, where those pages were still inside the
top-6 the model reads.

## How it works

```
PDF ──► text layer? ──yes──► page text ─┐
         │ no                            ├─► header + body lines ─► chunks (≤ 900 chars, header on every chunk)
         └──► render 220 dpi ─► RapidOCR ┘                               │
                                                     fastembed (bge-small) ─► pgvector
                                                     to_tsvector('simple')  ─► GIN
question ─► embed ─► cosine top-30 ─┐
         └► terms (id ×3, number ×2, word ×1, generic ×0.4) ─► weighted term match top-30 ─┤
                                                          RRF (k=60) + document-number boost ─► top-6 chunks
top-6 chunks ─► Claude document blocks, citations on ─► answer + [n] ─► file, page, quote
```

**Chunking keeps the header.** Business documents are tables under a header. A chunk cut
from the middle of a table knows neither its document number nor whether it is an invoice,
so every chunk starts with the page's identifying lines (title, number, dates) and then
carries a window of body lines ([`chunk.py`](src/ragdemo/ingest/chunk.py)).

**Lexical scoring is weighted, not ts_rank.** The retriever decides what a query term is
worth: a document number or SKU 3.0, an amount 2.0, a content word 1.0, words that appear in
every document ("invoice", "total") 0.4. A chunk scores the sum of the terms it matches. When
a chunk matches a document number the question names, it outranks every semantic neighbour
([`store.py`](src/ragdemo/store.py), [`retrieve.py`](src/ragdemo/retrieve.py)).

**OCR rows survive rotation.** A scan is rotated by a degree or so, which moves the cells
of one table row down by almost a row pitch between the left and the right edge of the page.
Grouping boxes by their raw y chains rows together. The page angle is estimated from each
box's nearest neighbour to the right, the boxes are levelled, then grouped
([`ocr.py`](src/ragdemo/ingest/ocr.py)). The recognizer also drops spaces inside a line;
a handful of respacing rules put back the ones that change tokenization.

**Citations are structural, not parsed.** Each chunk goes to the model as its own document
block with citations enabled; the response carries the quoted text and the document index,
which maps back to file and page. The page links open the PDF at that page
([`answer.py`](src/ragdemo/answer.py)).

**Refusals are a protocol.** The system prompt asks for a fixed token when the documents do
not hold the answer; the app turns it into "Not in the documents" and the eval counts it.

## Run it locally

```bash
docker compose up -d                 # Postgres 17 + pgvector on localhost:54329
uv sync                              # Python 3.12, uv
cp .env.example .env                 # ANTHROPIC_API_KEY, DATABASE_URL
uv run ragdemo ingest                # 40 PDFs -> 99 chunks, 5-7 min (OCR of 20 pages at full resolution)
uv run ragdemo search "What is the total due on invoice AEC-0201?"   # retrieval only
uv run ragdemo ask "What is the total due on invoice AEC-0201?"      # with the model
uv run ragdemo eval --retrieval-only # recall@k, no model calls
uv run ragdemo eval                  # the full table, about $1.50 on Claude Opus 5.5
uv run ragdemo serve                 # http://127.0.0.1:8000
uv run pytest                        # 70 tests; the store test uses the Docker database
```

`uv run ragdemo generate` renders the corpus again from the generator (same seed, same files).

## The corpus

Forty documents, synthetic on purpose: twelve fictional vendors in the US, UK and EU, one
buyer, 16 purchase orders and 24 invoices (14 of them against an order), six page layouts,
three currencies, four number formats, five date formats, 21 documents on two pages, 14
rendered as scans (rotated, noisy, JPEG, no text layer). Every value on every page is in
[`ground_truth.json`](data/corpus/ground_truth.json), which is where the golden questions come
from. There is no third-party data and no licence to think about, and the generator
([`generate.py`](src/ragdemo/corpus/generate.py)) can be pointed at another domain.

## Deploy

One image (`Dockerfile`), models baked in, the corpus baked in, the index built on the first
start. Azure Container Apps with scale to zero, Neon Free for Postgres, secrets in Key Vault,
GitHub Actions on push to `master` (tests → image to GHCR → `az containerapp update` →
`/api/health`). Two stages, as in my other services:

```powershell
az group create -n rg-rag-docs-demo -l germanywestcentral
az deployment group create -g rg-rag-docs-demo -f infra/main.bicep -p infra/main.parameters.json `
  -p adminObjectId=$(az ad signed-in-user show --query id -o tsv) deployApps=false
pwsh infra/secrets-to-keyvault.ps1 -Vault <keyVaultName>      # asks for the key and the Neon string, prints nothing
az deployment group create -g rg-rag-docs-demo -f infra/main.bicep -p infra/main.parameters.json `
  -p adminObjectId=<id> deployApps=true image=ghcr.io/ekotovqa/rag-docs-demo:<sha>
```

The workflow needs three repository variables (`AZURE_CLIENT_ID` from the deployment's
`deployClientId` output, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`) and the GHCR package set
to public (or a pull token added to the template, as cycling-coach does).

## Limits, honestly

- One question, one retrieval. "Which other invoices come from the same vendor as X" needs a
  second hop; the document-number boost makes X's own pages win. A planner step is the fix.
- OCR is good enough for retrieval (100% on scanned pages) and sometimes wrong on a digit
  (`68:30` for `68.30`); the answer eval reports accuracy on scans separately. In dense
  lines the recognizer drops spaces ("100mdrum"), which hides a term from the lexical side;
  the respacing rules cover the shapes that recur, not every one. The text detector can also
  drop a whole line between two tight neighbours; the full-resolution setting fixed the one
  case the eval found, a second detection pass at another scale would make it robust.
- The system prompt is short, below the model's minimum cacheable prefix, so prompt caching
  does not kick in here; the page shows cached tokens honestly (zero).
- The spend guard is in memory: it resets when the app scales to zero. Enough for a demo,
  not for a product.
- The image is 1.5 GB (ONNX Runtime, OpenCV for RapidOCR). A slimmer OCR engine would halve it.
