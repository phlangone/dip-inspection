# Robot-side integration (URScript)

The Universal Robots controller owns the physical motion sequence and requests
inspection stages through RTDE. The complete station-specific PolyScope/URScript
program is intentionally not distributed. The following illustrative snippet is
derived from the Python RTDE contract; it is not an excerpt from the production
program and has not been tested on the controller.

```python
# Request presence (4), OCR (1), side A (2), or side B (3).
# Return [status, destination]. A timeout returns status 3 (error).
def request_inspection(command):
  write_output_integer_register(24, 0)
  local elapsed = 0.0
  # Require idle acknowledgement to avoid accepting a stale response.
  while (read_input_integer_register(24) != 0) and (elapsed < 2.0):
    sleep(0.05)
    elapsed = elapsed + 0.05
  end
  if read_input_integer_register(24) != 0:
    return [3, 0]
  end
  write_output_integer_register(24, command)
  elapsed = 0.0
  local status = read_input_integer_register(24)
  while (status == 0) and (elapsed < 10.0):
    sleep(0.05)
    elapsed = elapsed + 0.05
    status = read_input_integer_register(24)
  end
  local destination = 0
  if (command == 3) and ((status == 1) or (status == 2)):
    destination = read_input_integer_register(25)
  end
  write_output_integer_register(24, 0)
  if (status != 1) and (status != 2):
    return [3, 0]
  end
  return [status, destination]
end
```

The caller must advance to a pickup only on a fresh presence response of `1`;
`2` means no part and `3` means error. Before another request, ensure the idle
command has actually been observed by the worker (allow several RTDE sampling
periods, and confirm the idle response). The caller must treat communication
loss, unexpected responses and invalid destinations as faults, never as motion
permission. Side B status alone is not the final classification; read register
25 for the final disposition. This is an application-level handshake, not a
safety-rated interlock. Motion, gripping, collision avoidance and cell safety
are outside this snippet and require separate commissioning.

See [architecture](architecture.md#rtde-contract-uno-q-perspective) for register
assignments, response values and the worker's request handling.
