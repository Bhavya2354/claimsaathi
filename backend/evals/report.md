# Evaluation (28 synthetic cases, 7 failed)

| Metric | Result |
| --- | --- |
| Settlement within 2% of the true amount | 76% |
| Median settlement error | 0.0% |
| Payable / not payable decision | 81% |
| Bill lines found | 100% |
| Bill lines labelled correctly | 100% |
| Audits passing the checker | 100% |
| Insurer reasons judged correctly | 83% |
| Appeal / accept recommendation | 83% |
| Letters passing the checker | 100% |
| Chat answers with the expected fact | 90% |
| Chat follow-up turns correct | 98% |
| Chat long answers correct | 100% |
| Chat answers citing a clause | 93% |
| Policy term read: cataract_sub_limit | 100% |
| Policy term read: copay | 100% |
| Policy term read: pre_existing_wait | 100% |
| Policy term read: proportionate_clause | 95% |
| Policy term read: room_rent | 100% |

## Median time per step (seconds)

| Step | Seconds |
| --- | --- |
| ingest | 3.6 |
| audit | 30.6 |
| review | 11.6 |
| letter | 38.5 |

## Models used

| Task | Model | Attempts | OK | Rate limited | Invalid output | Median ms |
| --- | --- | --- | --- | --- | --- | --- |
| fast | gemini:gemini-3.5-flash-lite | 34 | 34 | 0 | 0 | 939 |
| fast | groq:openai/gpt-oss-20b | 218 | 193 | 13 | 11 | 690 |
| reasoning | gemini:gemini-3.5-flash | 37 | 8 | 25 | 0 | 4975 |
| reasoning | gemini:gemini-3.8-flash | 23 | 16 | 4 | 2 | 5828 |
| reasoning | groq:openai/gpt-oss-120b | 185 | 119 | 66 | 0 | 1513 |
| reasoning | groq:qwen/qwen3.8-27b | 124 | 81 | 43 | 0 | 588 |
| reasoning | ollama:qwen3.5:9b | 6 | 0 | 0 | 0 | - |
| reasoning | openrouter:nvidia/nemotron-3-super-120b-a12b:free | 53 | 47 | 5 | 1 | 14378 |
| reasoning | openrouter:nvidia/nemotron-3-ultra-550b-a55b:free | 4 | 0 | 4 | 0 | - |
