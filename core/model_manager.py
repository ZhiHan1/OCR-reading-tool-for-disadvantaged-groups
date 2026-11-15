"""
模型管理器 - 管理本地翻译模型的添加、删除、启用等操作
"""
import os
import json
import shutil
from typing import List, Dict, Optional
from utils.paths import app_root, resolve_path, store_path, model_drop_root
from utils.model_discovery import ModelDiscovery, path_key


class ModelManager:
    """本地模型管理器"""

    def __init__(self, models_dir: str = "models/local"):
        """
        初始化模型管理器

        Args:
            models_dir: 模型存储目录
        """
        self.models_dir = str(resolve_path(models_dir))

        self.config_file = os.path.join(self.models_dir, "models_config.json")
        self.models: Dict[str, Dict] = {}
        self.active_model: Optional[str] = None
        self.active_model_path: Optional[str] = None  # 新增：当前使用的模型的真实路径

        # 确保目录存在
        os.makedirs(self.models_dir, exist_ok=True)
        print(f"[模型管理器] 模型目录: {self.models_dir}")

        # 加载配置
        self._load_config()
        self.discovery = ModelDiscovery(model_drop_root('translation'),os.path.join(self.models_dir,'scan_hidden.json'),app_root())

        # 记录表是空的（首次运行 / 失效记录刚被清理）就立刻做首次发现。
        # ModelDiscovery 要求文件稳定两轮才登记，只靠定时器要等 3~6 秒；
        # 连续跑两轮即可当场把 `模型/翻译/` 里已有的 GGUF 登记下来并自动启用，
        # 否则这段时间里「翻译可用」为假，自动翻译会静默失效。
        if not self.models:
            self.scan()
            self.scan()

    def scan(self):
        """扫描投放目录，登记新出现的 GGUF。

        新增模型时若当前没有活动模型，**自动启用第一个**——与 add_model()
        的既有约定一致。少了这一步的后果（2026-11-15 打包版实测）：
        `模型/翻译/` 里有 GGUF、记录表却是空的（首次运行或失效记录被清理后），
        scan() 会把模型加进列表但 active 仍是 None，`translation_usable()`
        判定「翻译不可用」，自动翻译静默失效——用户看到的就是「打包后翻译坏了」。
        """
        known={path_key(m['path']) for m in self.models.values()}
        added=[]
        for path in self.discovery.candidates('translation'):
            if path_key(path) in known:continue
            mid=path.stem
            if mid in self.models:mid += '_'+self.discovery.unique_id(path)
            self.models[mid]=dict(name=path.stem,path=str(path),file_name=path.name,enabled=False,type='gguf')
            known.add(path_key(path));added.append(mid)
        if added:
            if not self.active_model:
                first=added[0]
                self.models[first]['enabled']=True
                self.active_model=first
                self.active_model_path=self.models[first]['path']
                print(f"[模型管理器] 扫描后自动启用第一个模型: {first}")
            self._save_config()
        return added

    def _load_config(self):
        """加载模型配置"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    models = data.get('models', {})

                    # 路径规范化和有效性检查
                    valid_models = {}
                    for model_id, model_info in models.items():
                        model_path = model_info.get('path', '')

                        abs_path = str(resolve_path(model_path))
                        # 兼容旧目录簿中的裸文件名；新格式保留完整项目相对路径。
                        if not os.path.isabs(model_path) and not os.path.exists(abs_path):
                            legacy = os.path.join(self.models_dir, model_path)
                            if os.path.exists(legacy):
                                abs_path = legacy

                        # 规范化路径（统一使用正斜杠）
                        abs_path = os.path.abspath(abs_path)

                        # 检查文件是否存在。文件已被删/移走的记录必须丢弃：
                        # 以前这里照样塞进 valid_models，只打一条警告，结果设置页的
                        # 「本地模型」下拉里会列出根本打不开的模型（用户看到幽灵条目）。
                        # 丢弃后 active_model 若指向它，下面的重置逻辑会清空。
                        # 文件回来时不用慌：scan() 会在投放目录里重新发现并登记。
                        if os.path.exists(abs_path):
                            model_info['path'] = abs_path
                            valid_models[model_id] = model_info
                        else:
                            print(f"[模型管理器] 已移除失效记录（文件不存在）: {model_id} ({abs_path})")

                    self.models = valid_models
                    self.active_model = data.get('active_model')
                    self.active_model_path = self.models.get(self.active_model, {}).get('path')

                    # 如果活动模型不存在，重置
                    if self.active_model and self.active_model not in self.models:
                        print("[模型管理器] 警告：活动模型不存在，重置为 None")
                        self.active_model = None
                        self.active_model_path = None

                    # 有失效记录被剔除时立刻写回，否则磁盘上的旧配置会在下次启动
                    # 又读回来（每次启动都重复警告一次，下拉也治不干净）。
                    if len(valid_models) != len(models):
                        print(f"[模型管理器] 已清理 {len(models) - len(valid_models)} 条失效模型记录并写回配置")
                        self._save_config()

                    print(f"[模型管理器] 已加载 {len(self.models)} 个模型")
                    if self.active_model_path:
                        print(f"[模型管理器] 当前使用的模型路径: {self.active_model_path}")
            except Exception as e:
                print(f"[模型管理器] 加载配置失败: {e}")
                self.models = {}
                self.active_model = None
                self.active_model_path = None

    def _save_config(self):
        """保存模型配置"""
        try:
            data = {
                'models': {key: dict(info, path=store_path(info['path'])) for key, info in self.models.items()},
                'active_model': self.active_model,
                'active_model_path': store_path(self.active_model_path) if self.active_model_path else None
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print("[模型管理器] 配置已保存")
        except Exception as e:
            print(f"[模型管理器] 保存配置失败: {e}")

    def add_model(self, model_path: str, model_name: Optional[str] = None, copy_to_dir: bool = False) -> bool:
        """
        添加模型

        Args:
            model_path: 模型文件路径
            model_name: 模型名称（可选，默认使用文件名）
            copy_to_dir: 是否复制模型到管理目录（默认False，仅存储路径）

        Returns:
            是否添加成功
        """
        try:
            model_path = str(resolve_path(model_path))
            self.discovery.set_hidden(model_path,False)
            if not os.path.exists(model_path):
                print(f"[模型管理器] 模型文件不存在: {model_path}")
                return False

            # 检查是否为 GGUF 文件
            if not model_path.lower().endswith('.gguf'):
                print("[模型管理器] 不支持的模型格式，仅支持 .gguf 文件")
                return False

            # 使用文件名作为模型ID
            file_name = os.path.basename(model_path)
            model_id = os.path.splitext(file_name)[0]

            # 检查是否已存在
            if model_id in self.models:
                print(f"[模型管理器] 模型已存在: {model_id}")
                return False

            # 如果需要复制模型
            if copy_to_dir:
                dest_path = os.path.join(self.models_dir, file_name)
                if not os.path.exists(dest_path):
                    print("[模型管理器] 正在复制模型...")
                    shutil.copy2(model_path, dest_path)
                    print(f"[模型管理器] 模型已复制到: {dest_path}")
                model_file_path = dest_path
            else:
                # 存储绝对路径
                model_file_path = os.path.abspath(model_path)
                print(f"[模型管理器] 仅存储模型路径（不复制文件）: {model_file_path}")

            # 添加到模型列表
            self.models[model_id] = {
                'name': model_name or file_name,
                'path': model_file_path,
                'file_name': file_name,
                'enabled': False,  # 新添加的模型默认不启用
                'type': 'gguf'
            }

            # 如果是第一个模型，自动设为活动模型
            if not self.active_model:
                self.active_model = model_id
                self.active_model_path = model_file_path  # 设置当前使用的模型路径
                self.models[model_id]['enabled'] = True
                print(f"[模型管理器] 自动启用第一个模型: {model_id}")
                print(f"[模型管理器] 当前使用的模型路径已设置为: {model_file_path}")

            self._save_config()
            print(f"[模型管理器] 模型添加成功: {model_id}")
            return True

        except Exception as e:
            print(f"[模型管理器] 添加模型失败: {e}")
            return False

    def remove_model(self, model_id: str, delete_file: bool = False) -> bool:
        """
        删除模型

        Args:
            model_id: 模型ID
            delete_file: 是否删除模型文件

        Returns:
            是否删除成功
        """
        try:
            if model_id not in self.models:
                print(f"[模型管理器] 模型不存在: {model_id}")
                return False

            model_info = self.models[model_id]
            if not delete_file:self.discovery.set_hidden(model_info['path'],True)

            # 如果需要删除文件
            if delete_file:
                model_path = model_info['path']
                if os.path.exists(model_path) and model_path.startswith(self.models_dir):
                    os.remove(model_path)
                    print(f"[模型管理器] 已删除模型文件: {model_path}")

            # 从列表中删除
            del self.models[model_id]

            # 如果删除的是活动模型，切换到其他模型
            if self.active_model == model_id:
                if self.models:
                    self.active_model = list(self.models.keys())[0]
                    self.active_model_path = self.models[self.active_model]['path']
                else:
                    self.active_model = None
                    self.active_model_path = None

            self._save_config()
            print(f"[模型管理器] 模型已删除: {model_id}")
            return True

        except Exception as e:
            print(f"[模型管理器] 删除模型失败: {e}")
            return False

    def enable_model(self, model_id: str) -> bool:
        """
        启用模型（设为活动模型，禁用其他所有模型）

        Args:
            model_id: 模型ID

        Returns:
            是否成功
        """
        if model_id not in self.models:
            print(f"[模型管理器] 模型不存在: {model_id}")
            return False

        # 检查模型文件是否仍然存在
        model_path = self.models[model_id]['path']
        if not os.path.exists(model_path):
            print(f"[模型管理器] 模型文件已不存在: {model_path}")
            return False

        # 禁用所有其他模型（互斥）
        for mid in self.models:
            self.models[mid]['enabled'] = False

        # 启用目标模型
        self.active_model = model_id
        self.active_model_path = model_path  # 更新当前使用的模型路径
        self.models[model_id]['enabled'] = True

        self._save_config()
        print(f"[模型管理器] 已启用模型: {model_id}")
        print(f"[模型管理器] 当前使用的模型路径已设置为: {model_path}")
        return True

    def disable_model(self, model_id: str) -> bool:
        """
        禁用模型

        Args:
            model_id: 模型ID

        Returns:
            是否成功
        """
        if model_id not in self.models:
            print(f"[模型管理器] 模型不存在: {model_id}")
            return False

        self.models[model_id]['enabled'] = False

        # 如果禁用的是活动模型，切换到其他启用的模型
        if self.active_model == model_id:
            for mid, info in self.models.items():
                if info.get('enabled', True):
                    self.active_model = mid
                    self.active_model_path = info['path']
                    break
            else:
                self.active_model = None
                self.active_model_path = None

        self._save_config()
        print(f"[模型管理器] 已禁用模型: {model_id}")
        return True

    def get_all_models(self) -> List[Dict]:
        """
        获取所有模型列表

        只返回**文件确实存在**的模型。`_load_config` 已经会剔除失效记录，
        这里再兜一次底：运行期间文件被删、或配置被外部改写时，列表同样
        不能出现打不开的模型（否则设置页下拉就是一个点不开的幽灵条目）。

        Returns:
            模型信息列表
        """
        models_list = []
        for model_id, info in self.models.items():
            if not os.path.exists(info.get('path', '')):
                continue
            models_list.append({
                'id': model_id,
                'name': info['name'],
                'path': info['path'],
                'enabled': info.get('enabled', True),
                'is_active': model_id == self.active_model,
                'type': info.get('type', 'gguf')
            })
        return models_list

    def get_active_model(self) -> Optional[Dict]:
        """
        获取当前活动模型

        Returns:
            活动模型信息，如果没有则返回 None
        """
        if self.active_model and self.active_model in self.models:
            info = self.models[self.active_model]
            if not os.path.exists(info.get('path', '')):
                return None
            return {
                'id': self.active_model,
                'name': info['name'],
                'path': info['path'],
                'file_name': info.get('file_name', ''),  # 添加file_name字段
                'enabled': info.get('enabled', True),
                'type': info.get('type', 'gguf')
            }
        return None

    def get_active_model_path(self) -> Optional[str]:
        """
        直接获取当前使用的模型路径（最快速的方式）

        Returns:
            当前使用的模型的绝对路径，如果没有则返回 None
        """
        print("[ModelManager] get_active_model_path() 被调用")
        print(f"[ModelManager] active_model_path = {self.active_model_path}")
        return self.active_model_path
