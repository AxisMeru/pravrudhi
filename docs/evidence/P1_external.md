# External proof tier

Rendered from the ledger's `audit{kind: external_eval}` rows alone. Every row was scored by third-party tooling outside the kernel (tier: external); the result file is admitted by SHA-256. The kernel's own selection record is in the night documents.

| seq | track | condition | model | scorer | trust_remote_code | metric | value | ±  | n | file sha256 |
|---|---|---|---|---|---|---|---|---|---|---|
| 717 | M | base | Qwen/Qwen3-0.6B | lm-eval 0.4.9 | - | gsm8k exact_match,strict-match | 0.4086 | 0.0135 | 1319 | f5ec2ebf2ae4ec04 |
| 717 | M | base | Qwen/Qwen3-0.6B | lm-eval 0.4.9 | - | gsm8k exact_match,flexible-extract | 0.4064 | 0.0135 | 1319 | f5ec2ebf2ae4ec04 |
| 718 | M | adapter:c-0045 | Qwen/Qwen3-0.6B | lm-eval 0.4.9 | - | gsm8k exact_match,strict-match | 0.4898 | 0.0138 | 1319 | dd9805e97e36f919 |
| 718 | M | adapter:c-0045 | Qwen/Qwen3-0.6B | lm-eval 0.4.9 | - | gsm8k exact_match,flexible-extract | 0.4913 | 0.0138 | 1319 | dd9805e97e36f919 |
| 719 | H | base | Qwen/Qwen3-1.7B | evalplus 0.3.1 | - | humaneval+ pass@1 | 0.5915 | 0.0744 | 164 | 78e9c2b0dba677f6 |
| 900 | H | harness:c-0060 | Qwen/Qwen3-1.7B | evalplus 0.3.1 | - | humaneval+ pass@1 | 0.0854 | 0.0433 | 164 | e8c2b06f7b245d7b |
| 1068 | H | base-replicate | Qwen/Qwen3-1.7B | evalplus 0.3.1 | - | humaneval+ pass@1 | 0.5854 | 0.0746 | 164 | be642ec6506c0ee8 |
| 1838/1844 | H | base | Qwen/Qwen3-1.7B | evalplus 0.3.1 | - | humaneval+ pass@1 | 0.5976 | 0.0742 | 164 | b16b6500dd11fc98 |
| 1839 | H | harness:bestof4 | Qwen/Qwen3-1.7B | evalplus 0.3.1 | - | humaneval+ pass@1 | 0.6402 | 0.0727 | 164 | 11e35b253519d788 |
| 1840 | H | harness:retry3 | Qwen/Qwen3-1.7B | evalplus 0.3.1 | - | humaneval+ pass@1 | 0.6402 | 0.0727 | 164 | 1f4f64dd7cad7d1b |
| 1841/1845 | H | harness:combo | Qwen/Qwen3-1.7B | evalplus 0.3.1 | - | humaneval+ pass@1 | 0.6463 | 0.0724 | 164 | f66723b2fa7f3ed9 |
| 1842/1846 | H | base | Qwen/Qwen3-1.7B | evalplus 0.3.1 | - | mbpp+ pass@1 | 0.4921 | 0.0501 | 378 | 019dd55c9c3219bf |
| 1843/1847 | H | harness:combo | Qwen/Qwen3-1.7B | evalplus 0.3.1 | - | mbpp+ pass@1 | 0.6296 | 0.0485 | 378 | 23ea57b18e5b01e9 |
| 1848 | nyaya | base | Qwen/Qwen3-1.7B | lm-eval 0.4.9 | - | mmlu_jurisprudence acc,none | 0.6852 | 0.0449 | 108 | 43e2e6b301dc7439 |
| 1848 | nyaya | base | Qwen/Qwen3-1.7B | lm-eval 0.4.9 | - | mmlu_professional_law acc,none | 0.3918 | 0.0125 | 1534 | 43e2e6b301dc7439 |
| 3678 | nyaya | base | pravrudhi-kernel+prabhasa-nyaya-lean | pravrudhi-nyaya-validity  | - | nyaya_validity pass_rate | 1.0000 | 0.0000 | 600 | cf2b80a8fd7cf17d |
| 3679 | nyaya | base | pravrudhi-kernel+prabhasa-nyaya-lean | pravrudhi-nyaya-validity  | - | nyaya_validity pass_rate | 1.0000 | 0.0000 | 600 | 6e117b612e03f1a8 |

## Paired differences

- M gsm8k exact_match,strict-match: adapter:c-0045 − base = +0.0811 (base 0.4086±0.0135 [seq 717], adapter:c-0045 0.4898±0.0138, n=1319)
- M gsm8k exact_match,flexible-extract: adapter:c-0045 − base = +0.0849 (base 0.4064±0.0135 [seq 717], adapter:c-0045 0.4913±0.0138, n=1319)
- H humaneval+ pass@1: harness:c-0060 − base = -0.5061 (base 0.5915±0.0744 [seq 719], harness:c-0060 0.0854±0.0433, n=164)
- H humaneval+ pass@1: base-replicate − base = -0.0061 (base 0.5915±0.0744 [seq 719], base-replicate 0.5854±0.0746, n=164)
- H humaneval+ pass@1: harness:bestof4 − base = +0.0427 (base 0.5976±0.0742 [seq 1838], harness:bestof4 0.6402±0.0727, n=164)
- H humaneval+ pass@1: harness:retry3 − base = +0.0427 (base 0.5976±0.0742 [seq 1838], harness:retry3 0.6402±0.0727, n=164)
- H humaneval+ pass@1: harness:combo − base = +0.0488 (base 0.5976±0.0742 [seq 1838], harness:combo 0.6463±0.0724, n=164); paired: 15 harness:combo-only vs 7 base-only, exact McNemar p = 0.134
- H mbpp+ pass@1: harness:combo − base = +0.1376 (base 0.4921±0.0501 [seq 1842], harness:combo 0.6296±0.0485, n=378); paired: 59 harness:combo-only vs 7 base-only, exact McNemar p = 0.000

## Tensions

External rows are not kernel-executed: they carry tier `external`, not pratyakṣa in the kernel sense. Their standard errors are the scorer's own (lm-eval) or a Wilson half-width (EvalPlus), not the loop's σ_seed.
