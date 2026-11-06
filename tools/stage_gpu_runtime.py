"""补齐 ONNX CUDA 执行依赖，不复制模型，也不打包整个 CUDA SDK。"""
from pathlib import Path
import hashlib
import json
import shutil
import site
import sys

def stage(out):
    out=Path(out)
    destination=out/'_internal/gpu_runtime'
    destination.mkdir(parents=True,exist_ok=True)
    patterns={'cublas':['cublas64_12.dll','cublasLt64_12.dll'],
              'cuda_runtime':['cudart64_12.dll'], 'cufft':['cufft64_11.dll'],
              'cudnn':['cudnn*64_9.dll'], 'cuda_nvrtc':['nvrtc64_120_0.dll','nvrtc-builtins64_129.dll']}
    records=[]
    for package, globs in patterns.items():
        directory=next((Path(s)/'nvidia'/package/'bin' for s in site.getsitepackages() if (Path(s)/'nvidia'/package/'bin').is_dir()),None)
        if directory is None:raise RuntimeError('Missing NVIDIA runtime package: '+package)
        for pattern in globs:
            sources=sorted(directory.glob(pattern))
            if not sources:raise RuntimeError('Missing GPU library: '+pattern)
            for src in sources:
                target=destination/src.name
                if not target.exists() or target.stat().st_size!=src.stat().st_size:
                    shutil.copy2(src,target)
                with target.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                records.append(dict(name=src.name,size=src.stat().st_size,sha256=digest))
    (out/'gpu-runtime-manifest.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    return records

if __name__=='__main__':
    result=stage(sys.argv[1]);print('GPU_RUNTIME_READY',len(result),sum(i['size'] for i in result))
