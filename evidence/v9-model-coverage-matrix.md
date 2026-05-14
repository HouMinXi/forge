# Model Coverage Matrix: v9 pop_vlan Kernel Patch Review

Real-world evidence from reviewing a 2-patch Linux kernel OVS selftest
contribution (390 lines, Python + shell) with 4 independent review sources.

## Setup

- **Sashiko.dev** (online): Gemini 3.1 Pro, 9-stage automated pipeline
- **Forge pipeline** (local): Opus 4.6 with updated adversarial-qe dimensions
- **DeepSeek**: deepseek-chat via OpenAI-compatible API
- **Kimi K2**: kimi-k2.6 via Anthropic-compatible API

## Coverage Matrix

| Finding | True Bug? | Sashiko (Gemini) | Forge (Opus) | DeepSeek | Kimi K2 |
|---------|-----------|:---:|:---:|:---:|:---:|
| Commit message references nonexistent MAX_ENCAP_DEPTH | Yes | x | x | x | x |
| Masked VLAN format breaks round-trip parsing | Yes | x | x | x | - |
| int(val,16) vs int(v,0) base handling inconsistency | Medium | - | - | - | x |
| Round-trip mask granularity loss undocumented | Medium | - | - | - | x |
| cfi=0 edge case changes mask from 0xEFFF to 0xFFFF | Medium | - | - | x | - |
| Error message hardcodes key name instead of variable | Low | - | - | x | - |
| push_vlan forces CFI=1 undocumented | Nit | - | x | - | x |
| parse() return check scope description imprecise | P2 | - | - | - | x |

## False Positive Rates

| Source | Total Reported | True Bugs | False Positives | FP Rate |
|--------|---------------|-----------|-----------------|---------|
| Sashiko (Gemini) | 5 | 2 | 3 | **60%** |
| Forge (Opus) | 3 | 2 | 0 | **0%** |
| DeepSeek | 4 | 2 | ~1 | **~25%** |
| Kimi K2 | 5 | 2 | ~1 | **~20%** |

## Key Observations

1. **No single model found all 8 issues.** The union of all 4 sources covers
   everything; any individual source misses 3-5 findings.

2. **Unique findings per model:**
   - Kimi K2: 3 unique (int base inconsistency, mask granularity, parse scope)
   - DeepSeek: 2 unique (cfi=0 edge case, error message hardcoding)
   - Forge/Opus: 0 unique, but **zero false positives**
   - Sashiko/Gemini: 0 unique, **highest false positive rate**

3. **The new dismissal discipline and finding verification gate** (added to
   adversarial-qe from sashiko methodology) eliminated all false positives.
   Sashiko's own Stage 8 (false positive filtering) was less effective --
   3 FPs leaked through.

4. **Cross-model review is not optional for high-correctness work.** Even the
   best single source (Forge pipeline with 0% FP rate) missed 5 of 8 findings
   that other models caught.

## Recommendation

For kernel upstream contributions, run at minimum:
- Forge pipeline (zero FP, catches the critical bugs)
- One of DeepSeek or Kimi K2 (catches unique edge cases)
- Both if the patch touches parsing/formatting code (bidirectional correctness)
