# E1 fixture benchmark summary

- fixture: `scripts/fixtures/generation-benchmark/f1-stage12-x2.json`
- profile: token
- runs: 3 (succeeded: 2)
- success rate: 66.7% (threshold: 80.0%)
- wall p50 / p90: 790.4s / 981.3s (threshold on p90: 420s, measured over 3 run(s))
- verdict: FAIL — success rate 66.7% below --success-min 80.0%; wall p90 981.3s exceeds --wall-max-seconds 420s

## Tokens per successful deck

input avg: 162088, output avg: 42956 (n=2)

## Fit-ladder rung histogram

(none observed)

## Normalisations fired

(none observed)

## Quality-loop variant

(none observed)

## Terminal errors

authored_composition_safety_gate_failed: 1

## fallbackElementIds count

(not measured on any run)
