# Inference artifacts

Place the two Edge Impulse Linux executables here:

- `side_a.eim`
- `side_b.eim`

The worker expects these exact names. Use exports compatible with the UNO Q Linux/ARM64 environment and make them executable (`chmod +x models/*.eim`). The trained executables are not distributed with this repository. The model architecture, training settings and versions cannot be recovered from this source snapshot; record them alongside any released artifacts. Do not substitute an unverified export or assume the example thresholds transfer across models.
