"""只扫描投放目录，不加载或复制权重；稳定两轮后登记。"""
import hashlib
import json
import os
from pathlib import Path
from utils.paths import resolve_path, store_path

def path_key(path, base=None):
    return os.path.normcase(str(resolve_path(path, base)))

class ModelDiscovery:
    def __init__(self, root, state_file, base):
        self.root, self.state_file, self.base = Path(root), Path(state_file), Path(base)
        self.seen = {}

    def hidden(self):
        return set(json.loads(self.state_file.read_text(encoding='utf-8'))) if self.state_file.exists() else set()

    def set_hidden(self, path, hidden):
        items = self.hidden()
        key = store_path(path,self.base)
        if hidden: items.add(key)
        else: items.discard(key)
        self.state_file.parent.mkdir(parents=True,exist_ok=True)
        temp=self.state_file.with_suffix('.tmp')
        temp.write_text(json.dumps(sorted(items),ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temp,self.state_file)

    def candidates(self, kind):
        self.root.mkdir(parents=True,exist_ok=True)
        ignored,current,ready=self.hidden(),{},[]
        for number,(directory,dirs,files) in enumerate(os.walk(self.root,followlinks=False)):
            if number>=2000:break
            parent=Path(directory)
            dirs[:]=[d for d in dirs if not d.startswith('.') and d!='__pycache__' and not (parent/d).is_symlink() and not getattr(parent/d,'is_junction',lambda:False)()]
            if len(parent.relative_to(self.root).parts)>=5:dirs[:]=[]
            paths=[]
            if kind=='voice' and 'config.yaml' in files and 'model.pt' in files:
                paths=[(parent,[parent/'config.yaml',parent/'model.pt'])];dirs[:]=[]
            elif kind=='translation':
                paths=[(parent/f,[parent/f]) for f in files if f.lower().endswith('.gguf')]
            for path,parts in paths:
                if store_path(path,self.base) in ignored or any(p.is_symlink() for p in parts):continue
                try:
                    stats=tuple((p.stat().st_size,p.stat().st_mtime_ns) for p in parts)
                    if any(size==0 for size,mtime in stats):continue
                    if kind=='voice' and parts[0].stat().st_size>2*1024*1024:continue
                    if kind=='translation':
                        with path.open('rb') as stream:
                            if stream.read(4)!=b'GGUF':continue
                    key=path_key(path,self.base);current[key]=stats
                    if self.seen.get(key)==stats:ready.append(path)
                except OSError:continue
        self.seen=current
        return ready

    def unique_id(self,path):
        return hashlib.sha256(store_path(path,self.base).encode('utf-8')).hexdigest()[:12]
