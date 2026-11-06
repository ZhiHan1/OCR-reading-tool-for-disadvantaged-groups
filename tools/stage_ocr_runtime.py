"""从 RapidOCR 数据资源准备 Web UI 必需的小型 OCR 推理权重。"""
import hashlib
from pathlib import Path
import shutil
import sys
import rapidocr

FILES=('PP-OCRv6_det_small.onnx','ch_ppocr_mobile_v2.0_cls_mobile.onnx','PP-OCRv6_rec_small.onnx')

def stage(output):
    source=Path(rapidocr.__file__).resolve().parent/'models'
    target=Path(output)/'models/ocr'
    target.mkdir(parents=True,exist_ok=True)
    manifest=[]
    for name in FILES:
        src=source/name
        if not src.is_file():raise FileNotFoundError(f'RapidOCR runtime asset is missing: {src}')
        dst=target/name
        if not dst.is_file() or dst.stat().st_size!=src.stat().st_size:shutil.copy2(src,dst)
        digest=lambda p:hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
        a,b=digest(src),digest(dst)
        if a!=b:raise RuntimeError('OCR runtime asset checksum mismatch: '+name)
        manifest.append(dict(name=name,size=dst.stat().st_size,sha256=b))
    (Path(output)/'ocr-runtime-manifest.json').write_text(__import__('json').dumps(manifest,indent=2),encoding='utf-8')
    return manifest

if __name__=='__main__':
    entries=stage(sys.argv[1]);print('OCR_RUNTIME_READY',sum(x['size'] for x in entries),len(entries))
