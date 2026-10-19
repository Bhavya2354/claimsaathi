# Evaluation (28 synthetic cases, 0 failed)

| Metric | Result |
| --- | --- |
| Settlement within 2% of the true amount | 93% |
| Median settlement error | 0.0% |
| Payable / not payable decision | 100% |
| Bill lines found | 100% |
| Bill lines labelled correctly | 100% |
| Audits passing the checker | 100% |
| Insurer reasons judged correctly | 50% |
| Appeal / accept recommendation | 100% |
| Letters passing the checker | 50% |
| Chat answers with the expected fact | 82% |
| Chat follow-up turns correct | 99% |
| Chat long answers correct | 96% |
| Chat answers citing a clause | 100% |
| Policy term read: cataract_sub_limit | 100% |
| Policy term read: copay | 100% |
| Policy term read: pre_existing_wait | 100% |
| Policy term read: proportionate_clause | 100% |
| Policy term read: room_rent | 93% |

## Median time per step (seconds)

| Step | Seconds |
| --- | --- |
| ingest | 102.3 |
| audit | 0.0 |
| review | 16.0 |
| letter | 189.3 |

## Models used

| Task | Model | Attempts | OK | Rate limited | Invalid output | Median ms |
| --- | --- | --- | --- | --- | --- | --- |
| fast | ollama:qwen3.5-4b-8k | 237 | 237 | 0 | 0 | 827 |
| reasoning | ollama:qwen3.5-4b-8k | 438 | 438 | 0 | 0 | 6123 |
