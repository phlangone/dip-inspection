#!/usr/bin/env python3
"""Standalone presence diagnostic using the production camera and detector modules."""
import argparse
import json
from pathlib import Path
import statistics
import time

import cv2
from dip_inspection.vision.camera_manager import CameraManager
from dip_inspection.vision.presence_detector import PresenceDetector

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='store_true', help='Captura referência vazia e encerra.')
    parser.add_argument('--reference', default='empty_reference_new.jpg')
    parser.add_argument('--device', help='Sobrescreve o dispositivo da câmera (ex.: /dev/video0).')
    parser.add_argument('--samples', type=int, default=30, help='Número de amostras; 0 = contínuo.')
    parser.add_argument('--interval', type=float, default=0.5)
    parser.add_argument('--snapshot', action='store_true', help='Salva a última imagem de teste em presence_current.jpg.')
    args = parser.parse_args()
    if args.samples < 0 or args.interval < 0:
        parser.error('samples e interval devem ser não negativos.')
    reference = ROOT / args.reference
    settings = json.loads((ROOT / 'settings.json').read_text(encoding='utf-8'))
    config = settings['camera']
    if args.capture and reference.exists():
        parser.error(f'{reference.name} já existe; use outro nome em --reference para preservar o arquivo.')
    if not args.capture and not reference.is_file():
        parser.error(f'Referência ausente: {reference}. Execute --capture primeiro.')
    detector = None if args.capture else PresenceDetector(reference_path=str(reference))
    camera = CameraManager(device=args.device or config.get('device'),
                           camera_name=config.get('camera_name', 'USB 2.0 Camera'))
    scores = []
    positives = 0
    last_frame = None
    try:
        if args.capture:
            input('Trilho vazio, iluminação de operação e robô parado na pose de detecção? Enter para capturar: ')
        camera.connect()
        if args.capture:
            frame = camera.capture()
            if not cv2.imwrite(str(reference), frame):
                raise RuntimeError(f'Falha ao salvar {reference}')
            print(f'Referência salva: {reference} ({frame.shape[1]}x{frame.shape[0]})')
            return
        print(f'\nReferência: {reference.name} | pixel threshold: {detector.pixel_threshold} | '
              f'ROI: {detector.roi} | blur: {detector.blur_kernel} | '
              f'threshold presença: {detector.presence_ratio_threshold * 100:.2f}%')
        print('Ctrl+C encerra o teste.\n')
        while args.samples == 0 or len(scores) < args.samples:
            # Keep frames fresh between samples, as the worker does.
            deadline = time.monotonic() + (args.interval if scores else 0)
            while time.monotonic() < deadline:
                camera.get_frame()
            frame = camera.capture()
            result = detector.detect(frame)
            last_frame = frame
            score = result['score']
            scores.append(score)
            positives += int(result['present'])
            status = 'PRESENTE' if result['present'] else 'VAZIO'
            print(f'{len(scores):04d} | score={score:6.2f}% | '
                  f'limiar={result["threshold"]:5.2f}% | '
                  f'diff_media={result["mean_difference"]:6.2f} | {status}', flush=True)
    except KeyboardInterrupt:
        print('\nTeste interrompido.')
    finally:
        camera.close()
        if scores:
            print(f'\nAmostras: {len(scores)} | PRESENTE: {positives} | VAZIO: {len(scores)-positives}')
            print(f'Score min/média/max: {min(scores):.2f}% / {statistics.mean(scores):.2f}% / {max(scores):.2f}%')
        if args.snapshot and last_frame is not None:
            target = ROOT / 'presence_current.jpg'
            if cv2.imwrite(str(target), last_frame):
                print(f'Último frame salvo: {target}')
            else:
                print(f'Não foi possível salvar {target}')


if __name__ == '__main__':
    main()
