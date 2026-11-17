# Eval — 2026-11-17 07:24 UTC

103 questions: 93 answerable, 10 unanswerable.

## Retrieval

| Metric | Value |
|---|---|
| page recall@1 | 82% |
| document recall@1 | 90% |
| page recall@3 | 96% |
| document recall@3 | 99% |
| page recall@5 | 99% |
| document recall@5 | 99% |
| page recall@3, text-layer pages (n=56) | 95% |
| page recall@3, scanned pages (n=37) | 97% |
| retrieval time, mean | 262 ms |

## Answers

| Metric | Value |
|---|---|
| answer accuracy (answerable) | 99% |
| accuracy on text-layer documents | 98% |
| accuracy on scanned documents | 100% |
| correct refusals (unanswerable) | 100% |
| false refusals (answerable) | 1% |
| answers with at least one citation | 100% |
| answers citing a gold page | 100% |
| model latency, mean / p90 | 3018 / 4036 ms |
| cost per question, mean | $0.0145 |
| cost of the whole run | $1.50 |
| input tokens per question, mean | 3221 |
| model | claude-opus-5-5 |

## By question kind

| Kind | n | page recall@3 | accuracy |
|---|---|---|---|
| cross_vendor | 4 | 75% | 75% |
| delivery_date | 4 | 100% | 100% |
| doc_date | 5 | 100% | 100% |
| due_date | 8 | 100% | 100% |
| iban | 3 | 100% | 100% |
| po_ref | 6 | 100% | 100% |
| qty | 8 | 62% | 100% |
| shipping | 4 | 100% | 100% |
| tax | 3 | 100% | 100% |
| terms | 5 | 100% | 100% |
| total | 29 | 100% | 100% |
| unanswerable | 10 | - | 100% |
| unit_price | 6 | 100% | 100% |
| vendor | 8 | 100% | 100% |
