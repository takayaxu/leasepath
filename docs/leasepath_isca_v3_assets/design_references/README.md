# Method-figure design references

The LeasePath method figure is an original vector drawing. The user supplied the CacheWise overview as a reference for a full workflow plus two detailed panels. No CacheWise artwork, measurements, or scheduling behavior is copied into the LeasePath figure.

The GBDT subfigure was checked against these official sources on 2026-11-12:

- [LightGBM: leaf-wise (best-first) tree growth](https://lightgbm.readthedocs.io/en/stable/Features.html#leaf-wise-best-first-tree-growth). The official leaf-wise illustration was inspected for visual inspiration. The new diagram draws its own tree silhouettes; the archived model determines the stated depth/leaf limits.
- [scikit-learn: gradient boosting mathematical formulation](https://scikit-learn.org/stable/modules/ensemble.html#mathematical-formulation). Used to check the additive ensemble / loss-gradient explanation. The deployed implementation is LightGBM, not scikit-learn.
- [Lucide icons](https://lucide.dev/), `lucide-static@0.468.0`, served from [unpkg](https://unpkg.com/lucide-static@0.468.0/). Original SVGs, exact URLs and SHA256 hashes are in `../icons/source_manifest.json`. Applicable ISC and Feather/MIT notices are preserved in `../icons/lucide/LICENSE` and must accompany the source package.

The downloaded reference HTML and LightGBM illustration are local research materials; the source archive distributes this reference list, the original LeasePath artwork, and licensed Lucide symbols. It does not redistribute the downloaded reference pages/illustration.

Scientific facts come from the archived LeasePath model and implementation. See [the training contract](../../leasepath_gbdt_training_contract_2026-11-12.md). Training counts are actual initial-fit counts; request histories, interval-table examples, tree shapes, histogram heights, and equal-size-block candidate values are schematic. In particular, the mask row explains general as-of/censoring behavior; the initial collection's terminal snapshot labels were all U.
