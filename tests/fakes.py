"""Simulated worker implementations for supervisor tests."""
import sys
import time
import types
from unittest.mock import patch


def idle_worker(root, settings, events, controls, stop, shared, length, preview_lock):
    import os
    os.setsid()
    events.put({"kind": "update", "data": {"station_status": "WAITING_PART", "health": {"worker": "RUNNING"}}})
    while not stop.wait(0.05):
        events.put({"kind": "heartbeat", "data": time.monotonic()})


def crash_worker(*args):
    import os
    os.setsid()
    raise RuntimeError("crash proposital de teste")


class Camera:
    def __init__(self, **kwargs): pass
    def connect(self): pass
    def capture(self): return object()
    def get_frame(self): return object()
    def close(self): pass


class Presence:
    def __init__(self, **kwargs): pass
    def detect(self, frame): return {"present": True}


class OCR:
    def inspect(self, frame):
        return {"identified": True, "status": "IDENTIFICADO", "part_number": "TEST123N",
                "best": {"ocr_confidence": .98, "pn_score": 96}}


class Vision:
    def __init__(self): pass
    def inspect_side_a(self, frame): return {"anomaly": False, "score": 10., "threshold": 380., "status": "NORMAL"}
    def inspect_side_b(self, frame): return {"anomaly": False, "score": 15., "threshold": 402., "status": "NORMAL"}
    def stop(self): pass


class Telemetry:
    def __init__(self, **kwargs): self.connected=True; self.last_publish=None
    def connect(self): pass
    def publish(self, ur): self.last_publish=time.time()
    def close(self): pass


class UR:
    CMD_IDLE=0; CMD_OCR=1; CMD_SIDE_A=2; CMD_SIDE_B=3; CMD_PRESENCE=4
    RESP_IDLE=0; RESP_OK=1; RESP_NOK=2; RESP_ERROR=3; DEST_NONE=0
    safety_mode=1
    def __init__(self, *args):
        self.commands=iter([0,4,4,0,1,1,0,2,2,0,3,3,0]);self.command=0
    def connect(self): pass
    def receive(self):
        time.sleep(.03)
        self.command=next(self.commands,0)
        return object()
    def get_command(self): return self.command
    def get_safety_mode(self): return self.safety_mode
    def send_response(self, value): return True
    def send_destination(self, value): return True
    def close(self): pass


class UnsafeUR(UR):
    safety_mode=3


class Encoded:
    def tobytes(self): return b'jpeg-mock-test-only'


def hardware_worker(*args, unsafe=False):
    from dip_inspection.core.inspection_worker import worker_main
    modules={}
    for module, name, component in (("dip_inspection.vision.camera_manager","CameraManager",Camera), ("dip_inspection.vision.presence_detector","PresenceDetector",Presence),
                                   ("dip_inspection.vision.ocr_processor","OCRProcessor",OCR), ("dip_inspection.vision.vision_inspector","VisionInspector",Vision),
                                   ("dip_inspection.integrations.telemetry_publisher","TelemetryPublisher",Telemetry),("dip_inspection.integrations.ur_bridge","URBridge",UnsafeUR if unsafe else UR)):
        obj=types.ModuleType(module);setattr(obj,name,component);modules[module]=obj
    cv=types.ModuleType('cv2');cv.IMWRITE_JPEG_QUALITY=1;cv.imencode=lambda *args:(True,Encoded());modules['cv2']=cv
    with patch.dict(sys.modules, modules):
        worker_main(*args)


def unsafe_worker(*args):
    hardware_worker(*args,unsafe=True)

class UnknownOCR(OCR):
    def inspect(self, frame):
        result=super().inspect(frame);result['part_number']='UNKNOWN1';return result


class BadOCR(OCR):
    def inspect(self, frame):
        result=super().inspect(frame);result['identified']=False;result['status']='BAIXA_CONFIANCA';return result


class LostUR(UR):
    def receive(self): raise ConnectionError('RTDE desconectado em teste')


class LateUR(UR):
    def __init__(self,*args): self.commands=iter([0,1,0]);self.command=0


class BrokenCamera(Camera):
    def get_frame(self): raise RuntimeError('câmera perdida em teste')


def variant_worker(*args, ocr=OCR, ur=UR, camera=Camera):
    from dip_inspection.core.inspection_worker import worker_main
    modules={}
    for module,name,component in (("dip_inspection.vision.camera_manager","CameraManager",camera),("dip_inspection.vision.presence_detector","PresenceDetector",Presence),
                                  ("dip_inspection.vision.ocr_processor","OCRProcessor",ocr),("dip_inspection.vision.vision_inspector","VisionInspector",Vision),
                                  ("dip_inspection.integrations.telemetry_publisher","TelemetryPublisher",Telemetry),("dip_inspection.integrations.ur_bridge","URBridge",ur)):
        obj=types.ModuleType(module);setattr(obj,name,component);modules[module]=obj
    cv=types.ModuleType('cv2');cv.IMWRITE_JPEG_QUALITY=1;cv.imencode=lambda *args:(True,Encoded());modules['cv2']=cv
    with patch.dict(sys.modules,modules):worker_main(*args)


def unknown_worker(*args):variant_worker(*args,ocr=UnknownOCR)
def low_confidence_worker(*args):variant_worker(*args,ocr=BadOCR)
def lost_rtde_worker(*args):variant_worker(*args,ur=LostUR)
def late_request_worker(*args):variant_worker(*args,ur=LateUR)
def broken_camera_worker(*args):variant_worker(*args,camera=BrokenCamera)
