# Research assets and reproducibility

This source snapshot separates the application from station-specific configuration and research artifacts. It is not a complete experimental release.

## Dataset inventory

The supplied UNO Q archive contains the following JPEG counts in its collection folders:

| View | Normal | Anomalous (`anormal`) | Total |
| --- | ---: | ---: | ---: |
| Side A | 125 | 33 | 158 |
| Side B | 125 | 33 | 158 |
| Total | 250 | 66 | 316 |

These counts describe the files in the archive, not confirmed training/test splits, distinct physical parts, or the exact dataset used in the paper. Folder labels are preserved. The collector uses these paths; the image archive is kept separate from the source repository until its provenance and release permissions are reviewed.

## Release checklist

| Asset | Status in this source distribution |
| --- | --- |
| Application source and diagnostic tools | Included |
| Example product and station settings | Included; local credentials and robot IP removed from the exported settings |
| Dataset images | Separate archive for review; not part of the source repository |
| `models/side_a.eim`, `models/side_b.eim` | Not distributed with this repository |
| Robot-side URScript / PolyScope program | Not distributed; see the [illustrative handshake](urscript.md) |
| Empty-rail reference | Station-local; regenerate or release a clearly labeled calibration example |
| Package lockfile and OS version | Not established by the supplied archive |
| Training configuration and model metadata | Not supplied; export from Edge Impulse |
| Paper citation, final metrics and evaluation protocol | Add from the verified article and experimental records |
| Source license | MIT; external assets and third-party dependencies have separate terms |

For an archival release, record the hardware and software versions, camera pose and lighting, model preprocessing, training/validation split and threshold-selection procedure. Preserve the exact model exports used for the reported results. Report classification performance separately from physical sorting performance, and use a tagged source revision associated with the study.

## Technical references

- [Universal Robots RTDE guide](https://docs.universal-robots.com/tutorials/communication-protocol-tutorials/rtde-guide.html)
- [Universal Robots RTDE Python client](https://github.com/UniversalRobots/RTDE_Python_Client_Library)
- [Edge Impulse Linux Python SDK](https://github.com/edgeimpulse/linux-sdk-python)
- [RapidOCR](https://github.com/RapidAI/RapidOCR)
- [OpenCV](https://opencv.org/)
- [Eclipse Paho MQTT Python client](https://github.com/eclipse-paho/paho.mqtt.python)
