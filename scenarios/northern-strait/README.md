# Northern Strait

A three-Actor, six-turn contest over a strategic waterway. Arkania, the large coastal power,
wants the transit regime in its own hands and paid for by the ships that use it; Belvar, a
distant power with a naval task group on station, wants a durable foothold before its mandate
runs out; Cestia, the small state between them, lives on grain exports through the Strait and
on a coalition that will not tolerate foreign soldiers on its soil. Each turn is one week.

The package is deliberately small — one manifest, three Resources — and is written so that no
single forum satisfies every Actor: an Arkanian charter, a Belvaran basing agreement and a
Cestian transit guarantee each need someone else's consent. That makes it the scenario the
provider-backed smoke tests run (`tests/test_ai_smoke.py`, `tests/test_benchmark_smoke.py`)
and the one used by most retained development runs.

## Package

```text
scenario.yaml
resources/
  background.md        # public — regional history and the current dispute
  trade.csv            # public — shipping and trade volumes through the Strait
  arkania-intel.md     # private to Arkania — intelligence estimate of Belvar's task group
```


## Run it

```bash
uv run amaw run northern-strait
```

Every Actor gets one primary Argument per turn (`primary_arguments_per_actor: 1`) and may make
up to three Cons. The seed in the manifest (`424242`) makes the server's rolls reproducible for
a fixed model condition; `--seed` overrides it.
