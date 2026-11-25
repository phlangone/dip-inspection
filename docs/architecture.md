# Architecture and interfaces

## Inspection sequence

The supervisor runs a local Flask server and manages a separate worker process. The worker owns the camera, OCR and Edge Impulse runners, the RTDE client and MQTT telemetry. It waits for an idle robot command before accepting requests and processes each non-idle command once until the robot returns to idle.

| Step | Command | Operation |
| --- | --- | --- |
| Presence | 4 | Capture the rail and compare it with `empty_reference.jpg`. |
| OCR | 1 | Start a new part cycle, clear the previous destination, extract a part number and match an enabled product. |
| Side A | 2 | Run the side-A model and return its thresholded result. |
| Side B | 3 | Run the side-B model, compute the final destination and emit a completed-classification event. |

The physical robot program determines the actual motion sequence, retry behavior and reject handling. It is not distributed here; see the [illustrative URScript handshake](urscript.md).

### Presence detection

The detector converts the current and reference images to grayscale, optionally crops an ROI, and applies Gaussian blur. It computes the absolute pixel difference, thresholds it, then applies morphological opening and closing with a 3 × 3 kernel. Presence is reported when the fraction of changed pixels is at least the configured ratio.

| Default parameter | Value |
| --- | --- |
| ROI | Entire frame |
| Gaussian kernel | 5 × 5 |
| Pixel threshold | 25 (strictly greater than threshold) |
| Minimum changed ratio | 0.08 (8%) |

The reported score is the changed-pixel percentage, not a probability. Keep camera pose, resolution, exposure and illumination consistent with calibration. The camera manager requests 640 × 480 YUYV; actual driver settings may vary.

### OCR and product matching

RapidOCR extracts text and recognition confidence. A heuristic ranks candidates using confidence, character composition, length, known semiconductor family patterns and other textual features. The minimum plausibility score is 70/100 and the minimum margin between the two best candidates is 10 points. The selected normalized part number is then matched exactly to an enabled product. The heuristic score is not a calibrated probability or a fuzzy-match percentage.

### Visual anomaly detection

Each side uses an independent Edge Impulse `ImageImpulseRunner`, with preprocessing taken from the exported model settings. The application reads `visual_anomaly_mean`; a score greater than or equal to the configured side threshold is anomalous. Thresholds in the supplied station configuration were A = 550 and B = 620; these values are included as examples and must be validated against the exact exports. No training pipeline or trained model binaries are present in this snapshot.

## RTDE contract (UNO Q perspective)

Default TCP port: 30004. Example sampling frequency: 10 Hz.

| Register | Direction | Values |
| --- | --- | --- |
| `output_int_register_24` | UR → UNO Q | 0 idle; 1 OCR; 2 side A; 3 side B; 4 presence |
| `input_int_register_24` | UNO Q → UR | 0 idle; 1 OK; 2 NOK; 3 error |
| `input_int_register_25` | UNO Q → UR | 0 no destination; 1–4 product bins; 5 reject |

The worker clears the response when the robot returns to idle. Before returning a normal inspection result, it reads another RTDE packet and checks that the command has not changed and that less than nine seconds have elapsed. Approval requires an identified, enabled product and both sides evaluated as normal with no recorded cycle failure. The side-B response represents the side-B result; register 25 carries the final disposition. The robot must consume the appropriate registers and enforce its own timeout and safe motion logic.

The worker observes `safety_mode` and stops processing when it is outside the accepted modes (1 or 2). The software does not provide a safety-rated interlock, automatically unlock protective stops, or issue robot motion commands. Commission the physical cell independently.

## Monitoring and persistence

The dashboard exposes live camera preview, current inspection state, health, product settings and recent results. Administrative operations require a configured password hash. JSON state is written atomically with backups. Counts represent completed classifications, not verified physical deposits; abrupt interruption can lose the last classification before persistence.

MQTT is optional and asynchronous. Under the configured prefix (default `futurecom/ur`), `joints` publishes `timestamp` and `q`, `tcp` publishes `timestamp` and `pose`, and `status` publishes `timestamp` and `command`. Broker outages do not intentionally block inspections.
