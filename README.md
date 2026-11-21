# RWX Blast Radius selector

Uses per-test execution coverage from the pinned SQLGlot checkout, static fixture readers, dialect dependencies, and AST comparison of reconstructed comment-only edits. For each selected set it compares one through eight worker counts using longest-test-first placement and the official worker overhead.

The Docker image reads only the supplied patches and inventory. The coverage map contains pinned public SQLGlot source and test metadata. No contest answers are copied into the image.

Build and run under the limits in the official kit. To regenerate the map from the pinned checkout and the official test inventory, run build_coverage.py with --checkout, --inventory and --output, then run build_affinity.py with the checkout, base coverage map and final output path.
