# Changelog — `northern-strait`

Every version changes the sealed scenario hash, so runs under different versions are never
pooled. The hashes are in [`../versions.lock`](../versions.lock); recover an old version with
`git checkout scenario/northern-strait/<version> -- scenarios/northern-strait`. The next
version will be `1.1.0` or `2.0.0`: integer versions predate D-104.

## 1

2026-09-12 (T-044). First version: three Actors with triangular, zero-sum objectives over a
strategic waterway. Played by runs 3–6 and 10–15.

The `description` was rewritten into an in-world summary 36 minutes after release (`41f6cbe`)
without a version change. Only run 3 used the earlier text, whose scenario hash is
`394daae9e27d876b52474d2d5cde0c94c079efd734f189ff9088e60ad83350b2`. Runs 4 onward used the
locked content. Distinguish run 3 by its hash (D-104).
